from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from okcode.context import ArtifactStore, ContextConfig, ContextManager
from okcode.conversation import AgentConfig, ConversationSession
from okcode.errors import ProviderError, ProviderErrorKind
from okcode.mcp.models import McpCallResult, McpRemoteToolInfo
from okcode.mcp.tool import McpRemoteTool
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ChatMessage,
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
from okcode.prompt import TurnKind
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
) -> ConversationSession:
    actual_registry = registry or ToolRegistry()
    return ConversationSession(
        provider,
        actual_registry,
        ToolExecutor(actual_registry),
        config=config,
        context_manager=context_manager,
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
        AgentProgress("模型迭代 1/12", 1),
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
async def test_iteration_limit_stops_without_thirteenth_request_or_commit() -> None:
    tool = ControlledTool()
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("tool-1", "controlled", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "不应请求"))],
        ]
    )
    session = _session(provider, registry, config=AgentConfig(max_iterations=2))

    events = [event async for event in session.stream_turn("循环")]

    stopped = [event for event in events if isinstance(event, AgentStopped)]
    assert stopped[-1].reason is AgentStopReason.ITERATION_LIMIT
    assert len(provider.requests) == 2
    assert session.messages == ()


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
