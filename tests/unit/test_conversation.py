from __future__ import annotations

import asyncio

import pytest

from okcode.conversation import AgentConfig, ConversationSession
from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ChatMessage,
    Role,
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    TokenUsageReported,
    ToolCall,
    ToolCallRequested,
    ToolExecutionFinished,
    ToolExecutionStarted,
)
from okcode.prompt import TurnKind
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.registry import ToolRegistry
from tests.fakes import FakeProvider


def _session(
    provider: FakeProvider,
    registry: ToolRegistry | None = None,
    *,
    config: AgentConfig | None = None,
) -> ConversationSession:
    actual_registry = registry or ToolRegistry()
    return ConversationSession(
        provider,
        actual_registry,
        ToolExecutor(actual_registry),
        config=config,
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
