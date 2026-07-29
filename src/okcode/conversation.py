"""进程内 Agent Loop、Plan Mode 和原子提交。"""

from __future__ import annotations

import asyncio
import platform as host_platform
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from okcode.context import ContextManager, SummaryPlan, SummaryRequestFactory
from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ChatMessage,
    PermissionStatus,
    ProviderRequest,
    Role,
    StreamCompleted,
    TokenUsageReported,
    ToolCall,
    ToolCallRequested,
    ToolExecutionFinished,
    ToolExecutionStarted,
    TurnEvent,
)
from okcode.permissions.manager import PermissionManager
from okcode.prompt import (
    PromptBuildContext,
    PromptBuilder,
    PromptCachePolicy,
    TurnKind,
    enhance_tool_definitions,
)
from okcode.providers.base import LLMProvider
from okcode.tools.executor import PreparedToolCall, ToolExecutor
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
        prompt_builder: PromptBuilder | None = None,
        context_factory: Callable[[TurnKind, int, Sequence[ToolDefinition]], PromptBuildContext]
        | None = None,
        cache_policy: PromptCachePolicy | None = None,
        permissions: PermissionManager | None = None,
        context_manager: ContextManager | None = None,
        summary_factory: SummaryRequestFactory | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._executor = executor
        self._config = config or AgentConfig()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._context_factory = context_factory or _default_context_factory
        self._cache_policy = cache_policy or PromptCachePolicy()
        self._permissions = permissions
        self._context_manager = context_manager
        self._summary_factory = summary_factory or SummaryRequestFactory()
        self._messages: tuple[ChatMessage, ...] = ()
        self._saved_plan: SavedPlan | None = None

    @property
    def messages(self) -> tuple[ChatMessage, ...]:
        return self._messages

    @property
    def saved_plan(self) -> SavedPlan | None:
        return self._saved_plan

    @property
    def permission_mode(self) -> str:
        if self._permissions is None:
            return "default"
        return self._permissions.mode.value

    async def stream_turn(self, user_text: str) -> AsyncIterator[TurnEvent]:
        """流式执行一轮，并只在完整成功后提交历史。"""

        stripped = user_text.strip()
        if stripped == "/permissions" or stripped.startswith("/permissions "):
            async for event in self._handle_permissions_command(stripped):
                yield event
            return
        if stripped == "/compact":
            async for event in self._handle_compact_command():
                yield event
            return
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
            async for event in self._run_agent(
                user_message,
                tools,
                save_plan_task=task,
                turn_kind=TurnKind.PLAN,
            ):
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
            async for event in self._run_agent(
                user_message,
                self._registry.definitions(),
                turn_kind=TurnKind.DO,
            ):
                yield event
            return

        user_message = ChatMessage(role=Role.USER, content=user_text)
        async for event in self._run_agent(
            user_message,
            self._registry.definitions(),
            turn_kind=TurnKind.NORMAL,
        ):
            yield event

    async def _run_agent(
        self,
        user_message: ChatMessage,
        tools: Sequence[ToolDefinition],
        *,
        save_plan_task: str | None = None,
        turn_kind: TurnKind,
    ) -> AsyncIterator[TurnEvent]:
        pending: list[ChatMessage] = [user_message]
        consecutive_unknown_tools = 0
        if self._context_manager is not None:
            self._context_manager.record_user_message(user_message.content)

        for iteration in range(1, self._config.max_iterations + 1):
            yield AgentProgress(f"模型迭代 {iteration}/{self._config.max_iterations}", iteration)
            request = self._build_normal_request(pending, tools, turn_kind, iteration)
            if (
                self._context_manager is not None
                and self._context_manager.needs_automatic_compaction(request)
            ):
                yield AgentProgress("上下文接近窗口，正在压缩已完成历史。", iteration)
                stopped = await self._compact_automatically(pending)
                if stopped is not None:
                    yield stopped
                    return
                request = self._build_normal_request(pending, tools, turn_kind, iteration)

            completed: StreamCompleted | None = None
            async for event in self._provider.stream(request):
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
            if self._context_manager is not None:
                self._context_manager.record_normal_usage(request, completed.usage)
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

            normalized_results = tuple(results)
            if self._context_manager is not None:
                try:
                    normalized_results = self._context_manager.normalize_tool_results(results)
                except OSError:
                    yield AgentStopped(
                        AgentStopReason.CONTEXT_COMPACTION_FAILED,
                        "工具结果外置失败，已保留原历史并停止本轮任务。",
                    )
                    return
            pending.append(ChatMessage(role=Role.TOOL, tool_results=normalized_results))
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

    def _build_normal_request(
        self,
        pending: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        turn_kind: TurnKind,
        iteration: int,
    ) -> ProviderRequest:
        """构建带动态摘要补充的普通 Provider 请求。"""

        visible_tools = enhance_tool_definitions(tools)
        context = self._context_factory(turn_kind, iteration, visible_tools)
        if self._context_manager is not None:
            context = replace(
                context,
                additional_system_instructions=(
                    *context.additional_system_instructions,
                    *self._context_manager.system_instructions(),
                ),
            )
        prompt = self._prompt_builder.build(context, visible_tools)
        return ProviderRequest(
            messages=(*self._messages, *pending),
            tools=visible_tools,
            prompt=prompt,
            cache=self._cache_policy,
        )

    async def _handle_compact_command(self) -> AsyncIterator[TurnEvent]:
        """无条件执行一次手动摘要，不进入普通 Agent Loop。"""

        manager = self._context_manager
        if manager is None:
            yield AgentStopped(
                AgentStopReason.CONTEXT_COMPACTION_FAILED,
                "当前会话未启用上下文管理。",
            )
            return
        if manager.circuit_open:
            yield self._circuit_stopped()
            return
        plan = manager.plan_compaction(self._messages, (), force=True)
        if plan is None:
            yield AgentProgress("没有可压缩的已完成历史。")
            return
        yield AgentProgress("正在压缩会话历史。")
        stopped = await self._run_summary(plan)
        if stopped is not None:
            yield stopped
            return
        yield AgentProgress("上下文摘要已更新。")

    async def _compact_automatically(
        self,
        pending: Sequence[ChatMessage],
    ) -> AgentStopped | None:
        """为即将发送的普通请求压缩已完成历史。"""

        assert self._context_manager is not None
        if self._context_manager.circuit_open:
            return self._circuit_stopped()
        plan = self._context_manager.plan_compaction(self._messages, pending)
        if plan is None:
            return AgentStopped(
                AgentStopReason.CONTEXT_COMPACTION_FAILED,
                "上下文超出安全预算，但没有可安全摘要的已完成历史，已停止本轮任务。",
            )
        return await self._run_summary(plan)

    async def _run_summary(self, plan: SummaryPlan) -> AgentStopped | None:
        """消费内部摘要流；只在正式摘要校验成功后提交历史替换。"""

        assert self._context_manager is not None
        try:
            completed: StreamCompleted | None = None
            request = self._summary_factory.build(plan)
            async for event in self._provider.stream(request):
                if isinstance(event, StreamCompleted):
                    if completed is not None:
                        raise ProviderError(ProviderErrorKind.STREAM, "摘要流返回了多个完成事件。")
                    completed = event
                    continue
                if completed is not None:
                    raise ProviderError(ProviderErrorKind.STREAM, "摘要完成后出现了额外增量。")
            if completed is None:
                raise ProviderError(ProviderErrorKind.STREAM, "摘要流没有正常结束。")
            message = completed.message
            if message.role is not Role.ASSISTANT or message.tool_calls:
                raise ValueError("摘要响应不能包含工具调用。")
            summary = self._summary_factory.extract_final_summary(message.content, plan)
        except (ProviderError, ValueError):
            if self._context_manager.record_summary_failure():
                return self._circuit_stopped()
            return AgentStopped(
                AgentStopReason.CONTEXT_COMPACTION_FAILED,
                "上下文摘要失败，已保留原历史并停止本轮任务。",
            )
        self._messages = self._context_manager.commit_summary(plan, summary)
        return None

    @staticmethod
    def _circuit_stopped() -> AgentStopped:
        return AgentStopped(
            AgentStopReason.CONTEXT_SUMMARY_CIRCUIT_OPEN,
            "上下文摘要连续失败 3 次，当前会话已熔断，不再发起摘要请求。",
        )

    async def _execute_tool_calls(
        self,
        calls: Sequence[ToolCall],
    ) -> AsyncIterator[TurnEvent]:
        for batch in self._tool_batches(calls):
            prepared: list[tuple[int, PreparedToolCall]] = []
            results: dict[int, ToolExecutionResult] = {}
            for index, call in batch:
                yield ToolCallRequested(call, index)
                outcome = await self._executor.prepare(call)
                if isinstance(outcome, ToolExecutionResult):
                    results[index] = outcome
                else:
                    prepared.append((index, outcome))
            for _, item in prepared:
                yield ToolExecutionStarted(item.call.name)
            if len(prepared) == 1:
                index, item = prepared[0]
                results[index] = await self._executor.execute_prepared(item)
            elif prepared:
                executed = await asyncio.gather(
                    *(self._executor.execute_prepared(item) for _, item in prepared)
                )
                results.update(
                    {index: result for (index, _), result in zip(prepared, executed, strict=True)}
                )
            for index, _ in batch:
                yield ToolExecutionFinished(results[index])

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

    async def _handle_permissions_command(self, text: str) -> AsyncIterator[TurnEvent]:
        if self._permissions is None:
            yield AgentStopped(AgentStopReason.NO_SAVED_PLAN, "当前会话未启用权限系统。")
            return
        parts = text.split()
        if len(parts) == 1:
            yield self._permission_status()
            return
        if len(parts) == 2:
            try:
                self._permissions.set_mode(parts[1])
            except ValueError:
                yield self._permission_status("权限模式只能是 strict、default 或 allow。")
                return
            yield self._permission_status("权限模式已更新。")
            return
        yield self._permission_status("用法：/permissions [strict|default|allow]")

    def _permission_status(self, message: str | None = None) -> PermissionStatus:
        assert self._permissions is not None
        paths = self._permissions.paths
        return PermissionStatus(
            self.permission_mode,
            "default",
            str(paths.user),
            str(paths.project),
            str(paths.project_local),
            message,
        )


def _default_context_factory(
    turn_kind: TurnKind,
    iteration: int,
    tools: Sequence[ToolDefinition],
) -> PromptBuildContext:
    """从当前进程环境构造请求级动态提示上下文。"""

    return PromptBuildContext(
        workspace_root=str(Path.cwd()),
        platform=host_platform.platform(),
        current_date=date.today().isoformat(),
        available_tool_names=tuple(tool.name for tool in tools),
        turn_kind=turn_kind,
        iteration=iteration,
    )
