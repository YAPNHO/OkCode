from __future__ import annotations

import pytest

from okcode.conversation import ConversationSession
from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    ChatMessage,
    Role,
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolExecutionFinished,
    ToolExecutionStarted,
)
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import JSONValue, ToolDefinition, ToolErrorCode, ToolFailure, ToolOutput
from okcode.tools.registry import ToolRegistry
from tests.fakes import FakeProvider


def _session(provider: FakeProvider, registry: ToolRegistry | None = None) -> ConversationSession:
    actual_registry = registry or ToolRegistry()
    return ConversationSession(provider, actual_registry, ToolExecutor(actual_registry))


class ControlledTool:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.calls = 0
        self._definition = ToolDefinition(
            name="controlled",
            description="测试工具",
            input_schema={"type": "object", "additionalProperties": False},
            timeout_seconds=1,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: dict[str, JSONValue]) -> ToolOutput:
        self.calls += 1
        if self.fails:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "工具失败")
        return ToolOutput("工具成功", {"value": "ok"})


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
    assert events == [ThinkingDelta("分析"), TextDelta("答案")]
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
async def test_tool_turn_executes_once_commits_pair_and_stops() -> None:
    tool = ControlledTool()
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("tool-1", "controlled", "{}")
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))])
    session = _session(provider, registry)

    events = [event async for event in session.stream_turn("执行工具")]

    assert isinstance(events[0], ToolExecutionStarted)
    assert isinstance(events[1], ToolExecutionFinished)
    assert events[1].result.success is True  # type: ignore[union-attr]
    assert tool.calls == 1
    assert len(provider.requests) == 1
    assert [message.role for message in session.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
    ]


@pytest.mark.asyncio
async def test_failed_tool_result_is_still_committed_with_its_call() -> None:
    tool = ControlledTool(fails=True)
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("tool-1", "controlled", "{}")
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))])
    session = _session(provider, registry)

    events = [event async for event in session.stream_turn("执行工具")]

    assert isinstance(events[-1], ToolExecutionFinished)
    assert events[-1].result.success is False  # type: ignore[union-attr]
    assert session.messages[-1].tool_result is not None
    assert session.messages[-1].tool_result.error_code is ToolErrorCode.IO_ERROR
