from __future__ import annotations

import pytest

from okcode.context import ArtifactStore, ContextManager
from okcode.conversation import ConversationSession
from okcode.models import (
    AgentProgress,
    ChatMessage,
    Role,
    StreamCompleted,
    TextDelta,
    ToolCall,
    ToolExecutionFinished,
    ToolExecutionStarted,
)
from okcode.tools.defaults import build_default_registry
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolDefinition, ToolExecutionResult, ToolOutput, ToolSafety
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace
from tests.fakes import FakeProvider


class LargeResultTool:
    def __init__(self) -> None:
        self._definition = ToolDefinition(
            name="large_result",
            description="返回大量受控文本。",
            input_schema={"type": "object", "additionalProperties": False},
            timeout_seconds=1,
            safety=ToolSafety.READ_ONLY,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, _: dict[str, object]) -> ToolOutput:
        return ToolOutput("x" * 50_001)


@pytest.mark.asyncio
async def test_read_file_tool_turn_executes_once_and_commits_result(tmp_path) -> None:
    (tmp_path / "README.md").write_text("受控内容", encoding="utf-8")
    call = ToolCall("call-read", "read_file", '{"path":"README.md"}')
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [
                TextDelta("内容是受控内容"),
                StreamCompleted(ChatMessage(Role.ASSISTANT, "内容是受控内容")),
            ],
        ]
    )
    registry = build_default_registry(Workspace(tmp_path))
    session = ConversationSession(provider, registry, ToolExecutor(registry))

    events = [event async for event in session.stream_turn("读 README")]

    assert len(provider.requests) == 2
    assert isinstance(events[0], AgentProgress)
    assert any(isinstance(event, ToolExecutionStarted) for event in events)
    finished = [event for event in events if isinstance(event, ToolExecutionFinished)]
    assert finished[0].result.success is True
    assert [message.role for message in session.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
    result = session.messages[-2].tool_result
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


@pytest.mark.asyncio
async def test_large_tool_result_is_externalized_before_next_model_request(tmp_path) -> None:
    tool = LargeResultTool()
    registry = ToolRegistry()
    registry.register(tool)
    call = ToolCall("large-call", "large_result", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "已读取预览"))],
        ]
    )
    session = ConversationSession(
        provider,
        registry,
        ToolExecutor(registry, content_limit=100_000),
        context_manager=ContextManager(ArtifactStore(tmp_path, "tool-turn")),
    )

    _ = [event async for event in session.stream_turn("读取大结果")]

    result = provider.requests[1][-1].tool_result
    assert result is not None
    artifact = result.data["context_artifact"]
    assert isinstance(artifact, dict)
    path = artifact["path"]
    assert isinstance(path, str)
    assert (tmp_path / path).exists()
    assert len((tmp_path / path).read_text(encoding="utf-8")) > 50_000
    assert len(result.content) < 500
