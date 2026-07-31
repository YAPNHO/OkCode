"""进程内 Agent Loop、Plan Mode 和原子提交。"""

from __future__ import annotations

import asyncio
import copy
import logging
import platform as host_platform
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from okcode.commands.models import (
    CommandMemorySnapshot,
    CommandSessionSnapshot,
    CommandStatusSnapshot,
    RuntimeMode,
    ToolScope,
)
from okcode.context import ContextManager, SummaryPlan, SummaryRequestFactory
from okcode.errors import ProviderError, ProviderErrorKind
from okcode.hooks.models import HookContext, HookEvent
from okcode.hooks.runtime import HookRuntime
from okcode.memory.models import MemoryJob
from okcode.memory.store import MemoryStore
from okcode.memory.worker import MemoryWorker
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ChatMessage,
    CommandNotice,
    HookListEvent,
    PermissionStatus,
    ProviderRequest,
    Role,
    StreamCompleted,
    TokenUsage,
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
    SystemInstruction,
    TurnKind,
    enhance_tool_definitions,
)
from okcode.providers.base import LLMProvider
from okcode.sessions import SessionDescriptor, SessionJournal, SessionStore
from okcode.tools.executor import PreparedToolCall, ToolExecutor
from okcode.tools.models import ToolDefinition, ToolErrorCode, ToolExecutionResult, ToolSafety
from okcode.tools.registry import ToolRegistry

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    from okcode.skills.runtime import SkillRuntime


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Agent Loop 的安全兜底参数。"""

    # 单轮用户请求中，模型自主请求工具并回到模型的循环上限；不是用户对话轮数上限。
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
        session_journal: SessionJournal | None = None,
        session_store: SessionStore | None = None,
        memory_store: MemoryStore | None = None,
        memory_worker: MemoryWorker | None = None,
        model_name: str = "",
        workspace_root: Path | None = None,
        skill_runtime: SkillRuntime | None = None,
        hooks: HookRuntime | None = None,
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
        self._session_journal = session_journal
        self._session_store = session_store
        self._memory_store = memory_store
        self._memory_worker = memory_worker
        self._model_name = model_name
        self._workspace_root = workspace_root or Path.cwd()
        self._skill_runtime = skill_runtime
        self._hooks = hooks
        self._messages: tuple[ChatMessage, ...] = ()
        self._saved_plan: SavedPlan | None = None
        self._resumption_notice: str | None = None
        self._runtime_mode = RuntimeMode.DEFAULT
        self._cumulative_input_tokens = 0
        self._cumulative_output_tokens = 0
        self._turn_count = 0

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

    @property
    def runtime_mode(self) -> RuntimeMode:
        return self._runtime_mode

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def set_runtime_mode(self, mode: RuntimeMode) -> None:
        self._runtime_mode = mode

    def permission_string(self) -> str:
        return self._runtime_mode.value

    def hook_list_event(self) -> HookListEvent:
        if self._hooks is None:
            return HookListEvent((), str(self._workspace_root / ".okcode" / "hooks.yaml"))
        return HookListEvent(self._hooks.list_entries(), self._hooks.config_path)

    def session_snapshot(self) -> CommandSessionSnapshot:
        if self._session_journal is None:
            return CommandSessionSnapshot("", "")
        return CommandSessionSnapshot(
            self._session_journal.session_id,
            str(self._session_journal.path),
        )

    def memory_snapshot(self) -> CommandMemorySnapshot:
        if self._memory_store is None:
            return CommandMemorySnapshot((), ())
        return CommandMemorySnapshot(
            _memory_file_names(self._memory_store.paths.project_root),
            _memory_file_names(self._memory_store.paths.user_root),
        )

    def status_snapshot(self) -> CommandStatusSnapshot:
        memory = self.memory_snapshot()
        return CommandStatusSnapshot(
            self.permission_string(),
            self._cumulative_input_tokens,
            self._cumulative_output_tokens,
            len(self._registry.definitions()),
            len(memory.project_memory_files) + len(memory.user_memory_files),
            self._model_name,
            str(self._workspace_root),
        )

    def reset_session(self) -> CommandNotice:
        if self._session_journal is not None:
            self._session_journal.close()
        if self._session_store is not None:
            self._session_journal = self._session_store.create_journal()
        self._messages = ()
        self._saved_plan = None
        self._resumption_notice = None
        self._cumulative_input_tokens = 0
        self._cumulative_output_tokens = 0
        self._turn_count = 0
        if self._skill_runtime is not None:
            self._skill_runtime.activation_store.clear()
        if self._context_manager is not None:
            self._context_manager.restore_history(())
        return CommandNotice("已结束当前会话并开启新会话。")

    def list_resumable_sessions(self) -> tuple[SessionDescriptor, ...]:
        """返回当前项目可由 `/resume` 选择的会话摘要。"""

        if self._session_store is None:
            return ()
        return self._session_store.list_resumable()

    async def restore_session(self, session_id: str) -> AsyncIterator[TurnEvent]:
        """恢复一个历史会话，并在必要时先压缩一次历史。"""

        if self._session_store is None:
            yield AgentStopped(AgentStopReason.SESSION_RESTORE_FAILED, "当前会话未启用会话恢复。")
            return
        yield AgentProgress("正在恢复会话历史。")
        try:
            recovered = self._session_store.restore(session_id)
            journal = self._session_store.journal_for(session_id)
        except ValueError as exc:
            yield AgentStopped(AgentStopReason.SESSION_RESTORE_FAILED, str(exc))
            return
        if recovered.skipped_lines:
            yield AgentProgress(f"恢复时已跳过 {recovered.skipped_lines} 条损坏记录。")
        if recovered.was_truncated:
            yield AgentProgress("检测到不完整工具调用，已截断到最后一个合法消息边界。")

        previous_messages = self._messages
        previous_journal = self._session_journal
        previous_notice = self._resumption_notice
        previous_state = None
        if self._context_manager is not None:
            previous_state = copy.deepcopy(self._context_manager.state)
            self._context_manager.restore_history(recovered.messages)
        self._messages = recovered.messages
        self._session_journal = journal
        self._resumption_notice = self._gap_notice(recovered.updated_at)

        request = self._build_normal_request(
            (),
            self._registry.definitions(),
            TurnKind.NORMAL,
            1,
            include_resumption_notice=False,
        )
        if self._context_manager is not None and self._context_manager.needs_automatic_compaction(
            request
        ):
            yield AgentProgress("恢复历史接近上下文窗口，正在压缩。")
            stopped = await self._compact_recovered_history()
            if stopped is not None:
                self._messages = previous_messages
                self._session_journal = previous_journal
                self._resumption_notice = previous_notice
                assert previous_state is not None
                self._context_manager.state = previous_state
                yield stopped
                return
        yield AgentProgress("会话历史已恢复。")

    async def stream_turn(self, user_text: str) -> AsyncIterator[TurnEvent]:
        """兼容旧调用方：把文本当作普通用户消息执行。"""

        stripped = user_text.strip()
        if not stripped:
            return
        command, _, args = stripped.partition(" ")
        command = command.lower()
        if command == "/plan":
            async for event in self.stream_user_message(
                args.lstrip(),
                mode=RuntimeMode.PLAN,
                tool_scope=ToolScope.READ_ONLY,
            ):
                yield event
            return
        if command == "/do":
            async for event in self.stream_do_instruction():
                yield event
            return
        if command == "/compact":
            async for event in self.stream_manual_compaction():
                yield event
            return
        if command == "/permissions":
            async for event in self._handle_permissions_command(stripped):
                yield event
            return
        async for event in self.stream_user_message(user_text):
            yield event

    async def stream_user_message(
        self,
        user_text: str,
        *,
        mode: RuntimeMode | None = None,
        tool_scope: ToolScope | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """按当前或指定运行时模式执行一轮普通用户消息。"""

        actual_mode = mode or self._runtime_mode
        actual_scope = tool_scope or ToolScope.CURRENT_MODE
        if actual_scope is ToolScope.READ_ONLY or (
            actual_scope is ToolScope.CURRENT_MODE and actual_mode is RuntimeMode.PLAN
        ):
            tools = self._registry.definitions_by_safety(ToolSafety.READ_ONLY)
        else:
            tools = self._registry.definitions()
        turn_kind = TurnKind.PLAN if actual_mode is RuntimeMode.PLAN else TurnKind.NORMAL
        save_plan_task = user_text if actual_mode is RuntimeMode.PLAN else None
        user_message = ChatMessage(role=Role.USER, content=user_text)
        async for event in self._stream_hooked_agent(
            user_message,
            tools,
            save_plan_task=save_plan_task,
            turn_kind=turn_kind,
            runtime_mode=actual_mode,
        ):
            yield event

    async def stream_do_instruction(self) -> AsyncIterator[TurnEvent]:
        """执行本阶段前 /do 的外部行为。"""

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
        async for event in self._stream_hooked_agent(
            user_message,
            self._registry.definitions(),
            turn_kind=TurnKind.DO,
            runtime_mode=RuntimeMode.DEFAULT,
        ):
            yield event

    async def _stream_hooked_agent(
        self,
        user_message: ChatMessage,
        tools: Sequence[ToolDefinition],
        *,
        save_plan_task: str | None = None,
        turn_kind: TurnKind,
        runtime_mode: RuntimeMode,
    ) -> AsyncIterator[TurnEvent]:
        outcome = "completed"
        turn_index = self._turn_count + 1
        await self._dispatch_hook(
            HookContext(
                HookEvent.MESSAGE_USER,
                {
                    "message.content": user_message.content,
                    "runtime.mode": runtime_mode.value,
                },
            )
        )
        await self._dispatch_hook(
            HookContext(
                HookEvent.TURN_START,
                {
                    "turn.kind": turn_kind.value,
                    "turn.index": turn_index,
                    "runtime.mode": runtime_mode.value,
                    "message.content": user_message.content,
                },
            )
        )
        try:
            async for event in self._run_agent(
                user_message,
                tools,
                save_plan_task=save_plan_task,
                turn_kind=turn_kind,
                runtime_mode=runtime_mode,
            ):
                if isinstance(event, AgentStopped):
                    outcome = event.reason.value
                yield event
        except Exception:
            outcome = "error"
            raise
        finally:
            await self._dispatch_hook(
                HookContext(
                    HookEvent.TURN_END,
                    {
                        "turn.kind": turn_kind.value,
                        "turn.index": turn_index,
                        "turn.outcome": outcome,
                    },
                )
            )
            if self._hooks is not None:
                self._hooks.end_turn()

    async def stream_manual_compaction(self) -> AsyncIterator[TurnEvent]:
        """无条件执行一次手动摘要，不进入普通 Agent Loop。"""

        async for event in self._handle_compact_command():
            yield event

    async def _run_agent(
        self,
        user_message: ChatMessage,
        tools: Sequence[ToolDefinition],
        *,
        save_plan_task: str | None = None,
        turn_kind: TurnKind,
        runtime_mode: RuntimeMode,
    ) -> AsyncIterator[TurnEvent]:
        pending: list[ChatMessage] = [user_message]
        consecutive_unknown_tools = 0
        if self._context_manager is not None:
            self._context_manager.record_user_message(user_message.content)

        model_request = 1
        tool_iterations = 0
        while True:
            yield AgentProgress(f"模型请求 {model_request}", model_request)
            visible_tools = self._resolve_skill_tools(tools, turn_kind)
            request = self._build_normal_request(
                pending,
                visible_tools,
                turn_kind,
                model_request,
            )
            if (
                self._context_manager is not None
                and self._context_manager.needs_automatic_compaction(request)
            ):
                yield AgentProgress("上下文接近窗口，正在压缩已完成历史。", model_request)
                stopped = await self._compact_automatically(pending)
                if stopped is not None:
                    yield stopped
                    return
                visible_tools = self._resolve_skill_tools(tools, turn_kind)
                request = self._build_normal_request(
                    pending,
                    visible_tools,
                    turn_kind,
                    model_request,
                )

            if self._hooks is not None:
                self._hooks.mark_request_dispatched()
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
            self._record_token_usage(completed.usage)
            yield TokenUsageReported(completed.usage, model_request)

            assistant_message = completed.message
            if assistant_message.role is not Role.ASSISTANT:
                raise ProviderError(ProviderErrorKind.STREAM, "模型完成事件不是助手消息。")
            pending.append(assistant_message)
            await self._dispatch_hook(
                HookContext(
                    HookEvent.MESSAGE_ASSISTANT,
                    {
                        "message.content": assistant_message.content,
                        "message.tool_call_count": len(assistant_message.tool_calls),
                        "runtime.mode": runtime_mode.value,
                    },
                )
            )

            if not assistant_message.tool_calls:
                if not assistant_message.content.strip():
                    raise ProviderError(ProviderErrorKind.STREAM, "模型未返回可显示的正式回答。")
                self._messages = (*self._messages, *pending)
                self._turn_count += 1
                if save_plan_task is not None:
                    self._saved_plan = SavedPlan(save_plan_task, assistant_message.content)
                if self._session_journal is not None:
                    try:
                        self._session_journal.append(pending)
                    except OSError:
                        yield AgentStopped(
                            AgentStopReason.SESSION_ARCHIVE_FAILED,
                            "会话存档失败，但本轮回答已保留在当前进程中。",
                        )
                if self._memory_worker is not None:
                    self._memory_worker.submit(MemoryJob(tuple(pending)))
                return

            if tool_iterations >= self._config.max_iterations:
                yield AgentStopped(
                    AgentStopReason.ITERATION_LIMIT,
                    f"已达到 {self._config.max_iterations} 次自主工具迭代上限，已停止本轮任务。",
                )
                return
            tool_iterations += 1
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
            model_request += 1

    def _build_normal_request(
        self,
        pending: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        turn_kind: TurnKind,
        iteration: int,
        *,
        include_resumption_notice: bool = True,
    ) -> ProviderRequest:
        """构建带动态摘要补充的普通 Provider 请求。"""

        visible_tools = enhance_tool_definitions(tools)
        context = self._context_factory(turn_kind, iteration, visible_tools)
        additional_instructions = context.additional_system_instructions
        if include_resumption_notice and self._resumption_notice is not None:
            additional_instructions = (
                *additional_instructions,
                SystemInstruction("session_gap", self._resumption_notice, priority=95),
            )
            self._resumption_notice = None
        if self._context_manager is not None:
            context = replace(
                context,
                additional_system_instructions=(
                    *additional_instructions,
                    *self._context_manager.system_instructions(),
                ),
            )
        elif additional_instructions != context.additional_system_instructions:
            context = replace(context, additional_system_instructions=additional_instructions)
        if self._hooks is not None:
            context = replace(
                context,
                additional_system_instructions=(
                    *context.additional_system_instructions,
                    *self._hooks.system_instructions(),
                ),
            )
        prompt = self._prompt_builder.build(context, visible_tools)
        return ProviderRequest(
            messages=(*self._messages, *pending),
            tools=visible_tools,
            prompt=prompt,
            cache=self._cache_policy,
            model_override=(
                self._skill_runtime.activation_store.model_override()
                if self._skill_runtime is not None
                else None
            ),
        )

    def _resolve_skill_tools(
        self,
        base_tools: Sequence[ToolDefinition],
        turn_kind: TurnKind,
    ) -> tuple[ToolDefinition, ...]:
        """每轮依据最新快照计算模型可见工具，支持同轮按需激活。"""

        runtime = self._skill_runtime
        if runtime is None:
            return tuple(base_tools)
        names = runtime.activation_store.visible_tool_names(
            tuple(tool.name for tool in base_tools),
            load_skill_name="load_skill",
        )
        if turn_kind is TurnKind.PLAN:
            names = tuple(
                name
                for name in names
                if name == "load_skill"
                or (
                    (tool := self._registry.get(name)) is not None
                    and tool.definition.safety is ToolSafety.READ_ONLY
                )
            )
        return self._registry.definitions_by_names(names)

    async def _compact_recovered_history(self) -> AgentStopped | None:
        """恢复历史超预算时执行且仅执行一次摘要请求。"""

        assert self._context_manager is not None
        if self._context_manager.circuit_open:
            return self._circuit_stopped()
        plan = self._context_manager.plan_compaction(self._messages, ())
        if plan is None:
            return AgentStopped(
                AgentStopReason.SESSION_RESTORE_FAILED,
                "恢复历史超出上下文预算，且没有可安全压缩的消息。",
            )
        return await self._run_summary(plan)

    def _gap_notice(self, updated_at: datetime) -> str | None:
        store = self._session_store
        if store is None or not store.is_long_gap(updated_at):
            return None
        return "该恢复会话与上次活动相隔较久，请先确认当前文件和任务状态，不要假设前一轮仍在执行。"

    def _record_token_usage(self, usage: TokenUsage) -> None:
        if usage.input_tokens is not None:
            self._cumulative_input_tokens += usage.input_tokens
        if usage.output_tokens is not None:
            self._cumulative_output_tokens += usage.output_tokens

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
        await self._dispatch_hook(
            HookContext(
                HookEvent.CONTEXT_COMPACTED,
                {
                    "context.reason": "manual" if not plan.retained_history else "automatic",
                    "context.summary_length": len(summary),
                },
            )
        )
        return None

    async def _dispatch_hook(self, context: HookContext) -> None:
        if self._hooks is None:
            return
        try:
            await self._hooks.dispatch(context)
        except Exception as exc:
            _LOG.info("Hook 事件分发失败：%s", exc)

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


def _memory_file_names(root: Path) -> tuple[str, ...]:
    if not root.is_dir():
        return ()
    return tuple(sorted(path.name for path in root.glob("*.md") if path.is_file()))
