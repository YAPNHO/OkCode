from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from okcode.context import ArtifactStore, ContextConfig, ContextManager
from okcode.conversation import AgentConfig, ConversationSession
from okcode.errors import ProviderError, ProviderErrorKind
from okcode.mcp.models import McpCallResult, McpRemoteToolInfo
from okcode.mcp.tool import McpRemoteTool
from okcode.memory.models import (
    MemoryAction,
    MemoryCategory,
    MemoryIndexEntry,
    MemoryOperation,
    MemoryPaths,
    MemoryScope,
    MemoryUpdate,
)
from okcode.memory.store import MemoryStore
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ChatMessage,
    CommandNotice,
    Role,
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    TokenUsageReported,
    ToolCall,
    ToolCallRequested,
    ToolExecutionFinished,
    ToolExecutionStarted,
)
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import PermissionConfirmation, PermissionMode
from okcode.permissions.rules import PermissionPaths
from okcode.prompt import RuntimePromptContextFactory, TurnKind
from okcode.sessions import SessionConfig, SessionStore
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import (
    JSONValue,
    PermissionTarget,
    PermissionTargetKind,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace
from tests.fakes import FakeProvider


def _session(
    provider: FakeProvider,
    registry: ToolRegistry | None = None,
    *,
    config: AgentConfig | None = None,
    context_manager: ContextManager | None = None,
    session_store: SessionStore | None = None,
    memory_worker: object | None = None,
) -> ConversationSession:
    actual_registry = registry or ToolRegistry()
    return ConversationSession(
        provider,
        actual_registry,
        ToolExecutor(actual_registry),
        config=config,
        context_manager=context_manager,
        session_store=session_store,
        session_journal=session_store.create_journal() if session_store is not None else None,
        memory_worker=memory_worker,  # type: ignore[arg-type]
    )


class ControlledTool:
    def __init__(
        self,
        name: str = "controlled",
        *,
        fails: bool = False,
        safety: ToolSafety = ToolSafety.SIDE_EFFECT,
        delay: float = 0,
    ) -> None:
        self.name = name
        self.fails = fails
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._definition = ToolDefinition(
            name=name,
            description="测试工具",
            input_schema={"type": "object", "additionalProperties": False},
            timeout_seconds=1,
            safety=safety,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: dict[str, JSONValue]) -> ToolOutput:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls += 1
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fails:
                raise ToolFailure(ToolErrorCode.IO_ERROR, "工具失败")
            return ToolOutput("工具成功", {"value": "ok", "tool": self.name})
        finally:
            self.active -= 1


class ControlledCommandTool:
    def __init__(self) -> None:
        self.calls = 0
        self._definition = ToolDefinition(
            name="run_command",
            description="受控命令工具",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            timeout_seconds=1,
            permission_target=PermissionTarget(PermissionTargetKind.COMMAND, "command"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: dict[str, JSONValue]) -> ToolOutput:
        self.calls += 1
        return ToolOutput("命令成功", {"command": arguments["command"]})


class RecordingMemoryWorker:
    def __init__(self) -> None:
        self.jobs = []

    def submit(self, job: object) -> None:
        self.jobs.append(job)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.mark.asyncio
async def test_successful_turn_forwards_delta_and_commits_atomically() -> None:
    provider = FakeProvider(
        [
            ThinkingDelta("分析"),
            TextDelta("答案"),
            StreamCompleted(ChatMessage(Role.ASSISTANT, "答案")),
        ]
    )
    session = _session(provider)
    events = [event async for event in session.stream_turn("问题")]
    assert events[:3] == [
        AgentProgress("模型请求 1", 1),
        ThinkingDelta("分析"),
        TextDelta("答案"),
    ]
    assert isinstance(events[-1], TokenUsageReported)
    assert [message.content for message in session.messages] == ["问题", "答案"]
    assert provider.requests[0][-1].content == "问题"


@pytest.mark.asyncio
async def test_exception_rolls_back_turn() -> None:
    provider = FakeProvider([TextDelta("半句"), ProviderError(ProviderErrorKind.STREAM, "中断")])
    session = _session(provider)
    with pytest.raises(ProviderError):
        _ = [event async for event in session.stream_turn("问题")]
    assert session.messages == ()
    assert provider.stream_closed is True


@pytest.mark.asyncio
async def test_invalid_stream_rolls_back_turn() -> None:
    provider = FakeProvider([TextDelta("答案")])
    session = _session(provider)
    with pytest.raises(ProviderError, match="没有正常结束"):
        _ = [event async for event in session.stream_turn("问题")]
    assert session.messages == ()


@pytest.mark.asyncio
async def test_duplicate_completion_rolls_back_turn() -> None:
    answer = ChatMessage(Role.ASSISTANT, "答案")
    provider = FakeProvider([StreamCompleted(answer), StreamCompleted(answer)])
    session = _session(provider)
    with pytest.raises(ProviderError, match="多个"):
        _ = [event async for event in session.stream_turn("问题")]
    assert session.messages == ()


@pytest.mark.asyncio
async def test_tool_turn_continues_until_final_answer_and_commits() -> None:
    tool = ControlledTool()
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("tool-1", "controlled", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [TextDelta("完成"), StreamCompleted(ChatMessage(Role.ASSISTANT, "完成"))],
        ]
    )
    session = _session(provider, registry)

    events = [event async for event in session.stream_turn("执行工具")]

    assert any(isinstance(event, ToolCallRequested) for event in events)
    assert any(isinstance(event, ToolExecutionStarted) for event in events)
    finished = [event for event in events if isinstance(event, ToolExecutionFinished)]
    assert finished[0].result.success is True
    assert tool.calls == 1
    assert len(provider.requests) == 2
    assert [message.role for message in session.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]


@pytest.mark.asyncio
async def test_failed_tool_result_is_committed_when_final_answer_arrives() -> None:
    tool = ControlledTool(fails=True)
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("tool-1", "controlled", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "失败后总结"))],
        ]
    )
    session = _session(provider, registry)

    events = [event async for event in session.stream_turn("执行工具")]

    finished = [event for event in events if isinstance(event, ToolExecutionFinished)]
    assert finished[-1].result.success is False
    tool_message = session.messages[-2]
    assert tool_message.tool_result is not None
    assert tool_message.tool_result.error_code is ToolErrorCode.IO_ERROR


@pytest.mark.asyncio
async def test_agent_loop_calls_registered_mcp_tool_and_reinjects_result() -> None:
    class RemoteCaller:
        def __init__(self) -> None:
            self.arguments: list[dict[str, JSONValue]] = []

        async def call_tool(
            self,
            _: str,
            __: str,
            arguments: dict[str, JSONValue],
        ) -> McpCallResult:
            self.arguments.append(arguments)
            return McpCallResult(("远端结果",), {"structured_content": {"ok": True}})

    caller = RemoteCaller()
    tool = McpRemoteTool(
        McpRemoteToolInfo(
            "remote",
            "echo",
            "回显参数。",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
        caller,
    )
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("remote-call", "mcp__remote__echo", '{"text":"hello"}')
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "远端工具已完成。"))],
        ]
    )
    session = _session(provider, registry)

    _ = [event async for event in session.stream_turn("调用远端工具")]

    assert caller.arguments == [{"text": "hello"}]
    assert provider.requests[1][-1].tool_result is not None
    assert provider.requests[1][-1].tool_result.content == "远端结果"
    assert session.messages[-1].content == "远端工具已完成。"


@pytest.mark.asyncio
async def test_read_only_tools_run_concurrently_and_results_keep_order() -> None:
    first = ControlledTool("first", safety=ToolSafety.READ_ONLY, delay=0.05)
    second = ControlledTool("second", safety=ToolSafety.READ_ONLY, delay=0.05)
    registry = ToolRegistry()
    registry.register(first)
    registry.register(second)
    calls = (
        ToolCall("call-1", "first", "{}"),
        ToolCall("call-2", "second", "{}"),
    )
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_calls=calls))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "完成"))],
        ]
    )
    session = _session(provider, registry)

    _ = [event async for event in session.stream_turn("并发读")]

    assert first.max_active == 1
    assert second.max_active == 1
    result_ids = [result.tool_call_id for result in session.messages[-2].tool_results]
    assert result_ids == ["call-1", "call-2"]


@pytest.mark.asyncio
async def test_side_effect_tools_run_serially() -> None:
    first = ControlledTool("first", delay=0.02)
    second = ControlledTool("second", delay=0.02)
    registry = ToolRegistry()
    registry.register(first)
    registry.register(second)
    calls = (
        ToolCall("call-1", "first", "{}"),
        ToolCall("call-2", "second", "{}"),
    )
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_calls=calls))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "完成"))],
        ]
    )
    session = _session(provider, registry)

    _ = [event async for event in session.stream_turn("串行写")]

    assert first.max_active == 1
    assert second.max_active == 1
    assert first.calls == 1
    assert second.calls == 1


@pytest.mark.asyncio
async def test_unknown_tool_limit_stops_without_commit() -> None:
    call = ToolCall("missing-1", "missing", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
        ]
    )
    session = _session(provider)

    events = [event async for event in session.stream_turn("未知工具")]

    stopped = [event for event in events if isinstance(event, AgentStopped)]
    assert stopped[-1].reason is AgentStopReason.UNKNOWN_TOOL_LIMIT
    assert len(provider.requests) == 2
    assert session.messages == ()


@pytest.mark.asyncio
async def test_tool_iteration_limit_allows_final_answer_after_last_allowed_tool() -> None:
    tool = ControlledTool()
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("tool-1", "controlled", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "最终回答"))],
        ]
    )
    session = _session(provider, registry, config=AgentConfig(max_iterations=2))

    events = [event async for event in session.stream_turn("循环")]

    assert not any(isinstance(event, AgentStopped) for event in events)
    assert len(provider.requests) == 3
    assert tool.calls == 2
    assert [message.role for message in session.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
    assert session.messages[-1].content == "最终回答"


@pytest.mark.asyncio
async def test_tool_iteration_limit_stops_before_executing_next_tool_or_commit() -> None:
    tool = ControlledTool()
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("tool-1", "controlled", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "不应请求"))],
        ]
    )
    session = _session(provider, registry, config=AgentConfig(max_iterations=2))

    events = [event async for event in session.stream_turn("循环")]

    stopped = [event for event in events if isinstance(event, AgentStopped)]
    assert stopped[-1].reason is AgentStopReason.ITERATION_LIMIT
    assert len(provider.requests) == 3
    assert tool.calls == 2
    assert session.messages == ()


@pytest.mark.asyncio
async def test_direct_user_conversation_is_not_capped_by_tool_iteration_limit() -> None:
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "第一答"))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "第二答"))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "第三答"))],
        ]
    )
    session = _session(provider, config=AgentConfig(max_iterations=1))

    first = [event async for event in session.stream_turn("第一问")]
    second = [event async for event in session.stream_turn("第二问")]
    third = [event async for event in session.stream_turn("第三问")]

    assert not any(isinstance(event, AgentStopped) for event in (*first, *second, *third))
    assert len(provider.requests) == 3
    assert [message.content for message in session.messages] == [
        "第一问",
        "第一答",
        "第二问",
        "第二答",
        "第三问",
        "第三答",
    ]


@pytest.mark.asyncio
async def test_plan_uses_read_only_tools_and_do_uses_saved_plan_with_all_tools() -> None:
    read_tool = ControlledTool("read", safety=ToolSafety.READ_ONLY)
    write_tool = ControlledTool("write")
    registry = ToolRegistry()
    registry.register(read_tool)
    registry.register(write_tool)
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "计划内容"))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "执行完成"))],
        ]
    )
    session = _session(provider, registry)

    _ = [event async for event in session.stream_turn("/plan 做一件事")]
    _ = [event async for event in session.stream_turn("/do")]

    assert session.saved_plan is not None
    assert session.saved_plan.content == "计划内容"
    assert [definition.name for definition in provider.tools[0]] == ["read"]
    assert [definition.name for definition in provider.tools[1]] == ["read", "write"]
    assert "计划内容" in provider.requests[1][-1].content


@pytest.mark.asyncio
async def test_do_without_saved_plan_does_not_call_provider() -> None:
    provider = FakeProvider([])
    session = _session(provider)

    events = [event async for event in session.stream_turn("/do")]

    assert events == [
        AgentStopped(AgentStopReason.NO_SAVED_PLAN, "没有可执行的计划，请先使用 /plan 生成计划。")
    ]
    assert provider.requests == []


@pytest.mark.asyncio
async def test_request_prompt_is_not_committed_to_session_history() -> None:
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "完成"))])
    session = _session(provider)

    _ = [event async for event in session.stream_turn("解释当前项目")]

    request = provider.provider_requests[0]
    assert request.prompt.stable_system
    assert request.prompt.dynamic_system[0].kind == "environment"
    assert all("okcode-system-note" not in message.content for message in session.messages)


@pytest.mark.asyncio
async def test_plan_request_uses_task_mode_instruction_without_history_pollution() -> None:
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "计划内容"))])
    session = _session(provider)

    _ = [event async for event in session.stream_turn("/plan 研究项目")]

    request = provider.provider_requests[0]
    task_instruction = next(
        instruction
        for instruction in request.prompt.dynamic_system
        if instruction.kind == "task_mode"
    )
    assert "先通过只读工具" in task_instruction.content
    assert request.prompt.dynamic_system[0].kind == "environment"
    assert session.messages[0].content == "研究项目"
    assert TurnKind.PLAN.value not in session.messages[0].content


@pytest.mark.asyncio
async def test_permission_denial_is_returned_to_model_and_agent_loop_continues(
    tmp_path: Path,
) -> None:
    tool = ControlledCommandTool()
    registry = ToolRegistry()
    registry.register(tool)
    paths = PermissionPaths(
        user=tmp_path / "user.yaml",
        project=tmp_path / ".okcode" / "permissions.yaml",
        project_local=tmp_path / ".okcode" / "permissions.local.yaml",
    )
    permissions = PermissionManager(
        Workspace(tmp_path),
        (),
        paths,
        {"run_command"},
        mode=PermissionMode.DEFAULT,
        confirmer=lambda _: PermissionConfirmation.ONCE,
    )
    denied_call = ToolCall("denied", "run_command", '{"command":"shutdown /r /t 0"}')
    safe_call = ToolCall("safe", "run_command", '{"command":"git status"}')
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=denied_call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=safe_call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "已改用安全命令。"))],
        ]
    )
    session = ConversationSession(
        provider,
        registry,
        ToolExecutor(registry, permissions=permissions),
        permissions=permissions,
    )

    events = [event async for event in session.stream_turn("检查项目")]

    finished = [event.result for event in events if isinstance(event, ToolExecutionFinished)]
    assert finished[0].error_code is ToolErrorCode.PERMISSION_DENIED
    assert finished[0].data["permission_source"] == "blacklist"
    assert finished[1].success is True
    assert tool.calls == 1
    assert len(provider.requests) == 3
    first_result_message = provider.requests[1][-1]
    assert first_result_message.role is Role.TOOL
    assert first_result_message.tool_result is not None
    assert first_result_message.tool_result.error_code is ToolErrorCode.PERMISSION_DENIED
    assert [message.role for message in session.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]


@pytest.mark.asyncio
async def test_permissions_command_does_not_call_provider_or_change_history(tmp_path: Path) -> None:
    provider = FakeProvider([])
    registry = ToolRegistry()
    paths = PermissionPaths(
        user=tmp_path / "user.yaml",
        project=tmp_path / ".okcode" / "permissions.yaml",
        project_local=tmp_path / ".okcode" / "permissions.local.yaml",
    )
    permissions = PermissionManager(Workspace(tmp_path), (), paths, set())
    session = ConversationSession(
        provider,
        registry,
        ToolExecutor(registry, permissions=permissions),
        permissions=permissions,
    )

    status_events = [event async for event in session.stream_turn("/permissions strict")]
    invalid_events = [event async for event in session.stream_turn("/permissions unsafe")]

    assert provider.requests == []
    assert session.messages == ()
    assert session.permission_mode == "strict"
    assert status_events[0].current_mode == "strict"
    assert invalid_events[0].current_mode == "strict"
    assert invalid_events[0].message == "权限模式只能是 strict、default 或 allow。"


def _summary_response() -> str:
    headings = (
        "主要请求和意图",
        "关键技术概念",
        "文件和代码段",
        "错误和修复",
        "问题解决过程",
        "所有用户消息",
        "待办任务",
        "当前工作",
        "可能的下一步",
    )
    sections = [
        f"## {heading}\n"
        + ("{{ALL_USER_MESSAGES}}" if heading == "所有用户消息" else f"{heading}内容")
        for heading in headings
    ]
    return (
        "<analysis_draft>内部草稿</analysis_draft><formal_summary>"
        + "\n".join(sections)
        + "</formal_summary>"
    )


@pytest.mark.asyncio
async def test_automatic_summary_runs_before_normal_request_and_records_usage(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "初始回答"), TokenUsage(input_tokens=8))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, _summary_response()))],
            [
                StreamCompleted(
                    ChatMessage(Role.ASSISTANT, "压缩后继续"),
                    TokenUsage(input_tokens=9),
                )
            ],
        ]
    )
    context = ContextManager(
        ArtifactStore(tmp_path, "automatic"),
        ContextConfig(
            context_window_tokens=200,
            automatic_compaction_tokens=167,
            summary_output_reserve_tokens=20,
            safety_margin_tokens=13,
            retain_recent_tokens=1,
            retain_recent_messages=1,
        ),
    )
    session = _session(provider, context_manager=context)

    _ = [event async for event in session.stream_turn("第一轮")]
    events = [event async for event in session.stream_turn("x" * 1_000)]

    assert len(provider.provider_requests) == 3
    assert provider.provider_requests[1].tools == ()
    assert provider.provider_requests[2].messages[-1].content == "x" * 1_000
    dynamic_kinds = [item.kind for item in provider.provider_requests[2].prompt.dynamic_system]
    assert "context_summary" in dynamic_kinds
    assert "context_boundary" in dynamic_kinds
    assert not any(getattr(event, "delta", "") == "内部草稿" for event in events)
    assert context.state.estimate_anchor is not None
    assert context.state.estimate_anchor.input_tokens == 9


@pytest.mark.asyncio
async def test_compact_forces_summary_in_short_history_and_skips_empty_history(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "已有回答"))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, _summary_response()))],
        ]
    )
    session = _session(
        provider,
        context_manager=ContextManager(ArtifactStore(tmp_path, "manual")),
    )

    _ = [event async for event in session.stream_turn("短会话")]
    events = [event async for event in session.stream_turn("/compact")]

    assert len(provider.provider_requests) == 2
    assert provider.provider_requests[-1].tools == ()
    assert any(isinstance(event, AgentProgress) for event in events)

    empty_provider = FakeProvider([])
    empty_session = _session(
        empty_provider,
        context_manager=ContextManager(ArtifactStore(tmp_path, "empty")),
    )
    empty_events = [event async for event in empty_session.stream_turn("/compact")]
    assert empty_provider.requests == []
    assert empty_events == [AgentProgress("没有可压缩的已完成历史。")]


@pytest.mark.asyncio
async def test_three_summary_failures_open_circuit_without_changing_history(tmp_path: Path) -> None:
    provider = FakeProvider(
        [[StreamCompleted(ChatMessage(Role.ASSISTANT, "格式错误"))] for _ in range(3)]
    )
    session = _session(
        provider,
        context_manager=ContextManager(ArtifactStore(tmp_path, "circuit")),
    )
    session._messages = (ChatMessage(Role.USER, "已有请求"),)  # type: ignore[attr-defined]
    original = session.messages

    first = [event async for event in session.stream_turn("/compact")]
    second = [event async for event in session.stream_turn("/compact")]
    third = [event async for event in session.stream_turn("/compact")]
    fourth = [event async for event in session.stream_turn("/compact")]

    assert len(provider.provider_requests) == 3
    assert session.messages == original
    assert first[-1].reason is AgentStopReason.CONTEXT_COMPACTION_FAILED
    assert second[-1].reason is AgentStopReason.CONTEXT_COMPACTION_FAILED
    assert third[-1].reason is AgentStopReason.CONTEXT_SUMMARY_CIRCUIT_OPEN
    assert fourth == [
        AgentStopped(
            AgentStopReason.CONTEXT_SUMMARY_CIRCUIT_OPEN,
            "上下文摘要连续失败 3 次，当前会话已熔断，不再发起摘要请求。",
        )
    ]


@pytest.mark.asyncio
async def test_reset_session_closes_old_journal_and_clears_in_memory_state(tmp_path: Path) -> None:
    tokens = iter(("abcd", "bcde"))
    store = SessionStore(
        tmp_path,
        clock=lambda: datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
        token_factory=lambda: next(tokens),
    )
    provider = FakeProvider(
        [StreamCompleted(ChatMessage(Role.ASSISTANT, "done"), TokenUsage(3, 4))]
    )
    session = _session(provider, session_store=store)
    old_journal = session._session_journal  # type: ignore[attr-defined]

    _ = [event async for event in session.stream_turn("question")]
    notice = session.reset_session()
    new_journal = session._session_journal  # type: ignore[attr-defined]

    assert isinstance(notice, CommandNotice)
    assert session.messages == ()
    assert session.saved_plan is None
    assert session.status_snapshot().cumulative_input_tokens == 0
    assert session.status_snapshot().cumulative_output_tokens == 0
    assert old_journal is not None
    assert new_journal is not None
    assert old_journal.path != new_journal.path
    with pytest.raises(OSError):
        old_journal.append((ChatMessage(Role.USER, "late write"),))


@pytest.mark.asyncio
async def test_successful_turn_persists_messages_and_submits_memory_job(tmp_path: Path) -> None:
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "完成"))])
    worker = RecordingMemoryWorker()
    store = SessionStore(tmp_path, clock=lambda: datetime(2026, 7, 30, 10, tzinfo=UTC))
    session = _session(provider, session_store=store, memory_worker=worker)

    _ = [event async for event in session.stream_turn("记录这一轮")]

    descriptors = store.list_resumable()
    assert len(descriptors) == 1
    restored = store.restore(descriptors[0].id)
    assert [message.content for message in restored.messages] == ["记录这一轮", "完成"]
    assert len(worker.jobs) == 1
    assert [message.content for message in worker.jobs[0].messages] == ["记录这一轮", "完成"]


@pytest.mark.asyncio
async def test_new_session_injects_instruction_and_two_memory_indexes_without_old_messages(
    tmp_path: Path,
) -> None:
    memory_store = MemoryStore(MemoryPaths(tmp_path / "memory", tmp_path / "user-memory"))
    memory_store.apply(
        MemoryUpdate(
            (
                MemoryOperation(
                    MemoryScope.USER,
                    MemoryCategory.PREFERENCE,
                    MemoryAction.CREATE,
                    "user-note",
                    "User note",
                    "user memory",
                ),
                MemoryOperation(
                    MemoryScope.PROJECT,
                    MemoryCategory.PROJECT_KNOWLEDGE,
                    MemoryAction.CREATE,
                    "project-note",
                    "Project note",
                    "project memory",
                ),
            ),
            (MemoryIndexEntry("user-note", MemoryCategory.PREFERENCE, "user summary"),),
            (
                MemoryIndexEntry(
                    "project-note",
                    MemoryCategory.PROJECT_KNOWLEDGE,
                    "project summary",
                ),
            ),
        )
    )
    factory = RuntimePromptContextFactory(
        tmp_path,
        "project instruction",
        memory_store,
        current_date=lambda: date(2026, 7, 30),
        platform_name=lambda: "Windows",
    )
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "answer"))])
    registry = ToolRegistry()
    session = ConversationSession(
        provider,
        registry,
        ToolExecutor(registry),
        context_factory=factory,
    )

    _ = [event async for event in session.stream_turn("new question")]

    request = provider.provider_requests[0]
    assert [message.content for message in request.messages] == ["new question"]
    instructions = {item.kind: item.content for item in request.prompt.dynamic_system}
    assert instructions["custom"] == "project instruction"
    assert "user summary" in instructions["memory"]
    assert "project summary" in instructions["memory"]


@pytest.mark.asyncio
async def test_restore_keeps_new_session_empty_until_selected_history_is_used(
    tmp_path: Path,
) -> None:
    old_time = datetime(2026, 7, 28, 10, tzinfo=UTC)
    clock = MutableClock(old_time)
    store = SessionStore(
        tmp_path,
        config=SessionConfig(long_gap=timedelta(hours=1)),
        clock=clock,
    )
    old_journal = store.create_journal()
    old_journal.append((ChatMessage(Role.USER, "旧问题"), ChatMessage(Role.ASSISTANT, "旧回答")))
    clock.value = datetime(2026, 7, 30, 10, tzinfo=UTC)
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "继续回答"))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "再次回答"))],
        ]
    )
    session = _session(provider, session_store=store)

    events = [event async for event in session.restore_session(old_journal.session_id)]
    _ = [event async for event in session.stream_turn("继续任务")]
    _ = [event async for event in session.stream_turn("再次任务")]

    assert not any(isinstance(event, AgentStopped) for event in events)
    assert [message.content for message in provider.provider_requests[0].messages] == [
        "旧问题",
        "旧回答",
        "继续任务",
    ]
    dynamic_kinds = [item.kind for item in provider.provider_requests[0].prompt.dynamic_system]
    assert dynamic_kinds.count("session_gap") == 1
    assert all(
        item.kind != "session_gap" for item in provider.provider_requests[1].prompt.dynamic_system
    )


@pytest.mark.asyncio
async def test_restore_compacts_once_before_replacing_history(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 30, 10, tzinfo=UTC))
    store = SessionStore(tmp_path, clock=clock)
    journal = store.create_journal()
    journal.append(
        (
            ChatMessage(Role.USER, "旧请求" + "x" * 10_000),
            ChatMessage(Role.ASSISTANT, "旧回答"),
        )
    )
    provider = FakeProvider([[StreamCompleted(ChatMessage(Role.ASSISTANT, _summary_response()))]])
    context = ContextManager(
        ArtifactStore(tmp_path, "restore-compact"),
        ContextConfig(
            context_window_tokens=10_000,
            automatic_compaction_tokens=8_000,
            summary_output_reserve_tokens=1_000,
            safety_margin_tokens=1_000,
            chars_per_token=1,
            retain_recent_tokens=1,
            retain_recent_messages=1,
        ),
    )
    session = _session(provider, context_manager=context, session_store=store)

    events = [event async for event in session.restore_session(journal.session_id)]

    assert not any(isinstance(event, AgentStopped) for event in events)
    assert len(provider.provider_requests) == 1
    assert provider.provider_requests[0].tools == ()
    assert context.state.summary is not None
