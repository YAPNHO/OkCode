"""进程内 Agent Loop、Plan Mode 和原子提交。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ChatMessage,
    Role,
    StreamCompleted,
    TokenUsageReported,
    ToolCall,
    ToolCallRequested,
    ToolExecutionFinished,
    ToolExecutionStarted,
    TurnEvent,
)
from okcode.providers.base import LLMProvider
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolDefinition, ToolErrorCode, ToolExecutionResult, ToolSafety
from okcode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Agent Loop 的安全兜底参数。"""

    max_iterations: int = 12
    unknown_tool_limit: int = 2


@dataclass(frozen=True, slots=True)
class SavedPlan:
    """当前会话最近一次成功生成的计划。"""

    task: str
    content: str


class ConversationSession:
    """保存当前运行期间的已完成对话。"""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
        *,
        config: AgentConfig | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._executor = executor
        self._config = config or AgentConfig()
        self._messages: tuple[ChatMessage, ...] = ()
        self._saved_plan: SavedPlan | None = None

    @property
    def messages(self) -> tuple[ChatMessage, ...]:
        return self._messages

    @property
    def saved_plan(self) -> SavedPlan | None:
        return self._saved_plan

    async def stream_turn(self, user_text: str) -> AsyncIterator[TurnEvent]:
        """流式执行一轮，并只在完整成功后提交历史。"""

        stripped = user_text.strip()
        if stripped.startswith("/plan"):
            task = stripped.removeprefix("/plan").strip()
            if not task:
                yield AgentStopped(
                    AgentStopReason.NO_SAVED_PLAN,
                    "请在 /plan 后写明要规划的任务。",
                )
                return
            user_message = ChatMessage(role=Role.USER, content=task)
            tools = self._registry.definitions_by_safety(ToolSafety.READ_ONLY)
            async for event in self._run_agent(user_message, tools, save_plan_task=task):
                yield event
            return

        if stripped == "/do":
            if self._saved_plan is None:
                yield AgentStopped(
                    AgentStopReason.NO_SAVED_PLAN,
                    "没有可执行的计划，请先使用 /plan 生成计划。",
                )
                return
            user_message = ChatMessage(
                role=Role.USER,
                content="请执行当前会话最近一次计划：\n" + self._saved_plan.content,
            )
            async for event in self._run_agent(user_message, self._registry.definitions()):
                yield event
            return

        user_message = ChatMessage(role=Role.USER, content=user_text)
        async for event in self._run_agent(user_message, self._registry.definitions()):
            yield event

    async def _run_agent(
        self,
        user_message: ChatMessage,
        tools: Sequence[ToolDefinition],
        *,
        save_plan_task: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        pending: list[ChatMessage] = [user_message]
        consecutive_unknown_tools = 0

        for iteration in range(1, self._config.max_iterations + 1):
            yield AgentProgress(f"模型迭代 {iteration}/{self._config.max_iterations}", iteration)
            completed: StreamCompleted | None = None
            async for event in self._provider.stream((*self._messages, *pending), tools):
                if isinstance(event, StreamCompleted):
                    if completed is not None:
                        raise ProviderError(ProviderErrorKind.STREAM, "模型流返回了多个完成事件。")
                    completed = event
                    continue
                if completed is not None:
                    raise ProviderError(ProviderErrorKind.STREAM, "完成事件之后出现了额外增量。")
                yield event
            if completed is None:
                raise ProviderError(ProviderErrorKind.STREAM, "模型流没有正常结束。")
            yield TokenUsageReported(completed.usage, iteration)

            assistant_message = completed.message
            if assistant_message.role is not Role.ASSISTANT:
                raise ProviderError(ProviderErrorKind.STREAM, "模型完成事件不是助手消息。")
            pending.append(assistant_message)

            if not assistant_message.tool_calls:
                if not assistant_message.content.strip():
                    raise ProviderError(ProviderErrorKind.STREAM, "模型未返回可显示的正式回答。")
                self._messages = (*self._messages, *pending)
                if save_plan_task is not None:
                    self._saved_plan = SavedPlan(save_plan_task, assistant_message.content)
                return

            results: list[ToolExecutionResult] = []
            async for event in self._execute_tool_calls(assistant_message.tool_calls):
                if isinstance(event, ToolExecutionFinished):
                    results.append(event.result)
                    if event.result.error_code is ToolErrorCode.UNKNOWN_TOOL:
                        consecutive_unknown_tools += 1
                    else:
                        consecutive_unknown_tools = 0
                yield event

            pending.append(ChatMessage(role=Role.TOOL, tool_results=tuple(results)))
            if consecutive_unknown_tools >= self._config.unknown_tool_limit:
                yield AgentStopped(
                    AgentStopReason.UNKNOWN_TOOL_LIMIT,
                    f"连续 {self._config.unknown_tool_limit} 次调用未知工具，已停止本轮任务。",
                )
                return

        yield AgentStopped(
            AgentStopReason.ITERATION_LIMIT,
            f"已达到 {self._config.max_iterations} 次模型迭代上限，已停止本轮任务。",
        )

    async def _execute_tool_calls(
        self,
        calls: Sequence[ToolCall],
    ) -> AsyncIterator[TurnEvent]:
        for batch in self._tool_batches(calls):
            for index, call in batch:
                yield ToolCallRequested(call, index)
                yield ToolExecutionStarted(call.name)
            if len(batch) == 1:
                result = await self._executor.execute(batch[0][1])
                yield ToolExecutionFinished(result)
            else:
                results = await asyncio.gather(
                    *(self._executor.execute(call) for _, call in batch)
                )
                for result in results:
                    yield ToolExecutionFinished(result)

    def _tool_batches(
        self,
        calls: Sequence[ToolCall],
    ) -> tuple[tuple[tuple[int, ToolCall], ...], ...]:
        batches: list[tuple[tuple[int, ToolCall], ...]] = []
        read_batch: list[tuple[int, ToolCall]] = []
        for index, call in enumerate(calls):
            if self._is_read_only(call):
                read_batch.append((index, call))
                continue
            if read_batch:
                batches.append(tuple(read_batch))
                read_batch = []
            batches.append(((index, call),))
        if read_batch:
            batches.append(tuple(read_batch))
        return tuple(batches)

    def _is_read_only(self, call: ToolCall) -> bool:
        tool = self._registry.get(call.name)
        return tool is not None and tool.definition.safety is ToolSafety.READ_ONLY
