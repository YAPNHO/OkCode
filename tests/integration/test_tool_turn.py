from __future__ import annotations

import pytest

from okcode.conversation import ConversationSession
from okcode.models import (
    ChatMessage,
    Role,
    StreamCompleted,
    ToolCall,
    ToolExecutionFinished,
    ToolExecutionStarted,
)
from okcode.tools.defaults import build_default_registry
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolExecutionResult
from okcode.tools.workspace import Workspace
from tests.fakes import FakeProvider


@pytest.mark.asyncio
async def test_read_file_tool_turn_executes_once_and_commits_result(tmp_path) -> None:
    (tmp_path / "README.md").write_text("受控内容", encoding="utf-8")
    call = ToolCall("call-read", "read_file", '{"path":"README.md"}')
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))])
    registry = build_default_registry(Workspace(tmp_path))
    session = ConversationSession(provider, registry, ToolExecutor(registry))

    events = [event async for event in session.stream_turn("读 README")]

    assert len(provider.requests) == 1
    assert isinstance(events[0], ToolExecutionStarted)
    assert isinstance(events[1], ToolExecutionFinished)
    assert events[1].result.success is True  # type: ignore[union-attr]
    assert [message.role for message in session.messages] == [Role.USER, Role.ASSISTANT, Role.TOOL]
    result = session.messages[-1].tool_result
    assert result is not None
    assert result.data["content"] == "受控内容"


def test_tool_history_is_ready_for_both_provider_protocols() -> None:
    call = ToolCall("call-read", "read_file", '{"path":"README.md"}')
    result_message = ChatMessage(
        Role.TOOL,
        tool_result=ToolExecutionResult("call-read", "read_file", True, "读取成功", None),
    )
    messages = (
        ChatMessage(Role.USER, "读 README"),
        ChatMessage(Role.ASSISTANT, tool_call=call),
        result_message,
    )

    from okcode.providers.anthropic import AnthropicProvider
    from okcode.providers.openai import OpenAIProvider

    openai_history = OpenAIProvider._serialize_messages(messages)
    anthropic_history = AnthropicProvider._serialize_messages(messages)
    assert openai_history[1]["tool_calls"][0]["id"] == "call-read"  # type: ignore[index]
    assert openai_history[2]["tool_call_id"] == "call-read"  # type: ignore[index]
    assert anthropic_history[1]["content"][0]["type"] == "tool_use"  # type: ignore[index]
    assert anthropic_history[2]["content"][0]["type"] == "tool_result"  # type: ignore[index]
