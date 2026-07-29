from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
from anthropic import AsyncAnthropic

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    ChatMessage,
    ProviderConfig,
    ProviderProtocol,
    ProviderRequest,
    Role,
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    ToolCall,
)
from okcode.prompt import PromptBuildContext, PromptBuilder, PromptCachePolicy, TurnKind
from okcode.providers.anthropic import AnthropicProvider
from okcode.tools.models import ToolDefinition, ToolErrorCode, ToolExecutionResult
from tests.helpers.sse import ChunkStream


class FakeBlock:
    def __init__(self, block_type: str, **values: str) -> None:
        self.type = block_type
        for name, value in values.items():
            setattr(self, name, value)

    def model_dump(self, *, exclude_none: bool = True) -> dict[str, str]:
        return {
            "type": self.type,
            **{key: value for key, value in self.__dict__.items() if key != "type"},
        }


class FakeAnthropicStream:
    def __init__(self, events: list[object], final_message: object) -> None:
        self._events = iter(events)
        self._final_message = final_message
        self.closed = False

    async def __aenter__(self) -> FakeAnthropicStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    def __aiter__(self) -> AsyncIterator[object]:
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def get_final_message(self) -> object:
        return self._final_message


class FakeMessages:
    def __init__(self, streams: list[FakeAnthropicStream | Exception]) -> None:
        self._streams = iter(streams)
        self.calls: list[dict[str, object]] = []

    def stream(self, **kwargs: object) -> FakeAnthropicStream:
        self.calls.append(kwargs)
        result = next(self._streams)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAnthropicClient:
    def __init__(self, streams: list[FakeAnthropicStream | Exception]) -> None:
        self.messages = FakeMessages(streams)
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


def _config(*, thinking: bool = True, prompt_cache: bool = False) -> ProviderConfig:
    return ProviderConfig(
        name="claude",
        protocol=ProviderProtocol.ANTHROPIC,
        model="claude-test",
        base_url="https://api.anthropic.com",
        api_key="secret",
        thinking=thinking,
        prompt_cache=prompt_cache,
    )


def _final() -> object:
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[
            FakeBlock("thinking", thinking="分析", signature="signature"),
            FakeBlock("redacted_thinking", data="encrypted"),
            FakeBlock("text", text="答案"),
        ],
    )


def _tool() -> ToolDefinition:
    return ToolDefinition(
        name="read_file",
        description="读取文件",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        timeout_seconds=5,
    )


def _request(
    messages: list[ChatMessage],
    tools: list[ToolDefinition] | None = None,
    *,
    cache: bool = False,
) -> ProviderRequest:
    visible_tools = tuple(tools or ())
    prompt = PromptBuilder().build(
        PromptBuildContext(
            workspace_root="D:/workspace",
            platform="Windows",
            current_date="2026-07-29",
            available_tool_names=tuple(tool.name for tool in visible_tools),
            turn_kind=TurnKind.NORMAL,
        ),
        visible_tools,
    )
    return ProviderRequest(
        messages=tuple(messages),
        tools=visible_tools,
        prompt=prompt,
        cache=PromptCachePolicy(enabled=cache),
    )


async def _collect(
    provider: AnthropicProvider,
    messages: list[ChatMessage],
    tools: list[ToolDefinition] | None = None,
) -> list[object]:
    return [event async for event in provider.stream(messages, tools or ())]


@pytest.mark.asyncio
async def test_thinking_events_and_private_state_round_trip() -> None:
    first = FakeAnthropicStream(
        [
            SimpleNamespace(type="thinking", thinking="分析"),
            SimpleNamespace(type="text", text="答案"),
            SimpleNamespace(type="message_stop"),
        ],
        _final(),
    )
    second = FakeAnthropicStream(
        [SimpleNamespace(type="text", text="下一轮"), SimpleNamespace(type="message_stop")],
        SimpleNamespace(stop_reason="end_turn", content=[FakeBlock("text", text="下一轮")]),
    )
    client = FakeAnthropicClient([first, second])
    provider = AnthropicProvider(_config(), client=client)

    events = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    completed = events[-1]
    assert events[:2] == [ThinkingDelta("分析"), TextDelta("答案")]
    assert isinstance(completed, StreamCompleted)
    assert completed.message.content == "答案"
    assert completed.message.provider_state[0]["signature"] == "signature"  # type: ignore[index]
    assert completed.message.provider_state[1]["data"] == "encrypted"  # type: ignore[index]
    assert client.messages.calls[0]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert client.messages.calls[0]["max_tokens"] == 4096

    _ = await _collect(
        provider,
        [ChatMessage(Role.USER, "问题"), completed.message, ChatMessage(Role.USER, "追问")],
    )
    replayed = client.messages.calls[1]["messages"][1]["content"]  # type: ignore[index]
    assert replayed[0]["signature"] == "signature"
    assert replayed[1]["data"] == "encrypted"


@pytest.mark.asyncio
async def test_thinking_disabled_omits_parameter() -> None:
    stream = FakeAnthropicStream(
        [SimpleNamespace(type="text", text="答案"), SimpleNamespace(type="message_stop")],
        SimpleNamespace(stop_reason="end_turn", content=[FakeBlock("text", text="答案")]),
    )
    client = FakeAnthropicClient([stream])
    provider = AnthropicProvider(_config(thinking=False), client=client)
    _ = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    assert "thinking" not in client.messages.calls[0]


@pytest.mark.asyncio
async def test_anthropic_request_separates_system_blocks_and_cache_control() -> None:
    stream = FakeAnthropicStream(
        [SimpleNamespace(type="text", text="答案"), SimpleNamespace(type="message_stop")],
        SimpleNamespace(stop_reason="end_turn", content=[FakeBlock("text", text="答案")]),
    )
    client = FakeAnthropicClient([stream])
    provider = AnthropicProvider(_config(prompt_cache=True), client=client)
    request_data = _request([ChatMessage(Role.USER, "解释文件")], [_tool()], cache=True)

    _ = [event async for event in provider.stream(request_data)]

    request = client.messages.calls[0]
    system = request["system"]  # type: ignore[assignment]
    assert system[0]["text"] == request_data.prompt.stable_system  # type: ignore[index]
    assert system[0]["cache_control"]["type"] == "ephemeral"  # type: ignore[index]
    assert "<okcode-system-note" in system[1]["text"]  # type: ignore[index]
    assert "cache_control" not in system[1]  # type: ignore[operator]
    assert request["tools"][0]["cache_control"]["type"] == "ephemeral"  # type: ignore[index]


@pytest.mark.asyncio
async def test_anthropic_cache_usage_is_reported_without_estimation() -> None:
    stream = FakeAnthropicStream(
        [
            SimpleNamespace(
                type="message_start",
                usage=SimpleNamespace(
                    input_tokens=20,
                    output_tokens=0,
                    cache_read_input_tokens=12,
                    cache_creation_input_tokens=8,
                ),
            ),
            SimpleNamespace(type="text", text="答案"),
            SimpleNamespace(type="message_stop"),
        ],
        SimpleNamespace(stop_reason="end_turn", content=[FakeBlock("text", text="答案")]),
    )
    provider = AnthropicProvider(_config(), client=FakeAnthropicClient([stream]))

    events = [event async for event in provider.stream(_request([ChatMessage(Role.USER, "问题")]))]

    completed = events[-1]
    assert isinstance(completed, StreamCompleted)
    assert completed.usage.cache.read_tokens == 12
    assert completed.usage.cache.write_tokens == 8
    assert completed.usage.cache.available is True


@pytest.mark.asyncio
async def test_anthropic_missing_cache_usage_is_marked_unavailable() -> None:
    stream = FakeAnthropicStream(
        [
            SimpleNamespace(
                type="message_start",
                usage=SimpleNamespace(input_tokens=20, output_tokens=0),
            ),
            SimpleNamespace(type="text", text="答案"),
            SimpleNamespace(type="message_stop"),
        ],
        SimpleNamespace(stop_reason="end_turn", content=[FakeBlock("text", text="答案")]),
    )
    provider = AnthropicProvider(_config(), client=FakeAnthropicClient([stream]))

    events = [event async for event in provider.stream(_request([ChatMessage(Role.USER, "问题")]))]

    completed = events[-1]
    assert isinstance(completed, StreamCompleted)
    assert completed.usage.cache.available is False
    assert completed.usage.cache.read_tokens is None
    assert completed.usage.cache.write_tokens is None


@pytest.mark.asyncio
async def test_missing_message_stop_is_stream_error() -> None:
    stream = FakeAnthropicStream([], _final())
    provider = AnthropicProvider(_config(), client=FakeAnthropicClient([stream]))
    with pytest.raises(ProviderError) as raised:
        _ = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    assert raised.value.kind is ProviderErrorKind.STREAM


@pytest.mark.asyncio
async def test_anthropic_close_is_idempotent() -> None:
    client = FakeAnthropicClient([])
    provider = AnthropicProvider(_config(), client=client)
    await provider.aclose()
    await provider.aclose()
    assert client.close_count == 1


def _anthropic_event(event: str, data: str) -> bytes:
    return f"event: {event}\ndata: {data}\n\n".encode()


@pytest.mark.asyncio
async def test_current_anthropic_sdk_accumulates_stream() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkStream(
                [
                    _anthropic_event(
                        "message_start",
                        '{"type":"message_start","message":{"id":"m","type":"message",'
                        '"role":"assistant","content":[],"model":"claude-test",'
                        '"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":1}}}',
                    ),
                    _anthropic_event(
                        "content_block_start",
                        '{"type":"content_block_start","index":0,"content_block":'
                        '{"type":"thinking","thinking":"","signature":""}}',
                    ),
                    _anthropic_event(
                        "content_block_delta",
                        '{"type":"content_block_delta","index":0,"delta":'
                        '{"type":"thinking_delta","thinking":"分析"}}',
                    ),
                    _anthropic_event(
                        "content_block_delta",
                        '{"type":"content_block_delta","index":0,"delta":'
                        '{"type":"signature_delta","signature":"sig"}}',
                    ),
                    _anthropic_event(
                        "content_block_stop", '{"type":"content_block_stop","index":0}'
                    ),
                    _anthropic_event(
                        "content_block_start",
                        '{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
                    ),
                    _anthropic_event(
                        "content_block_delta",
                        '{"type":"content_block_delta","index":1,"delta":'
                        '{"type":"text_delta","text":"答案"}}',
                    ),
                    _anthropic_event(
                        "content_block_stop", '{"type":"content_block_stop","index":1}'
                    ),
                    _anthropic_event(
                        "message_delta",
                        '{"type":"message_delta","delta":{"stop_reason":"end_turn",'
                        '"stop_sequence":null},"usage":{"output_tokens":2}}',
                    ),
                    _anthropic_event("message_stop", '{"type":"message_stop"}'),
                ]
            ),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncAnthropic(
        api_key="test-key",
        base_url="https://mock.example",
        http_client=http_client,
        max_retries=0,
    )
    provider = AnthropicProvider(_config(), client=client)
    events = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    await provider.aclose()

    assert events[0:2] == [ThinkingDelta("分析"), TextDelta("答案")]
    assert isinstance(events[-1], StreamCompleted)
    assert events[-1].message.content == "答案"


@pytest.mark.asyncio
async def test_anthropic_input_json_fragments_become_one_tool_call() -> None:
    start_block = FakeBlock("tool_use", id="tool-1", name="read_file", input={})
    final_message = SimpleNamespace(
        stop_reason="tool_use",
        content=[FakeBlock("tool_use", id="tool-1", name="read_file", input={"path": "note.txt"})],
    )
    stream = FakeAnthropicStream(
        [
            SimpleNamespace(type="content_block_start", index=0, content_block=start_block),
            SimpleNamespace(type="input_json", index=0, partial_json='{"path":"'),
            SimpleNamespace(type="input_json", index=0, partial_json='note.txt"}'),
            SimpleNamespace(type="message_stop"),
        ],
        final_message,
    )
    client = FakeAnthropicClient([stream])
    provider = AnthropicProvider(_config(), client=client)

    events = await _collect(provider, [ChatMessage(Role.USER, "读文件")], [_tool()])

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, StreamCompleted)
    assert completed.message.tool_call == ToolCall("tool-1", "read_file", '{"path":"note.txt"}')
    request = client.messages.calls[0]
    assert request["tool_choice"] == {"type": "auto"}
    assert request["tools"][0]["input_schema"] == _tool().input_schema  # type: ignore[index]


@pytest.mark.asyncio
async def test_anthropic_returns_all_tool_calls_and_rejects_invalid_tool_calls() -> None:
    multiple_final = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            FakeBlock("tool_use", id="one", name="read_file", input={}),
            FakeBlock("tool_use", id="two", name="read_file", input={}),
        ],
    )
    invalid_final = SimpleNamespace(
        stop_reason="tool_use",
        content=[FakeBlock("tool_use", id="one", name="read_file", input={})],
    )
    multiple = FakeAnthropicStream([SimpleNamespace(type="message_stop")], multiple_final)
    invalid = FakeAnthropicStream(
        [
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=invalid_final.content[0],
            ),
            SimpleNamespace(type="input_json", index=0, partial_json="{"),
            SimpleNamespace(type="message_stop"),
        ],
        invalid_final,
    )
    provider = AnthropicProvider(_config(), client=FakeAnthropicClient([multiple, invalid]))

    events = await _collect(provider, [ChatMessage(Role.USER, "问题")], [_tool()])
    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, StreamCompleted)
    assert completed.message.tool_call == ToolCall("one", "read_file", "{}")
    assert completed.message.tool_calls == (
        ToolCall("one", "read_file", "{}"),
        ToolCall("two", "read_file", "{}"),
    )
    assert completed.message.provider_state == (
        {"type": "tool_use", "id": "one", "name": "read_file", "input": {}},
        {"type": "tool_use", "id": "two", "name": "read_file", "input": {}},
    )

    with pytest.raises(ProviderError, match="合法 JSON"):
        _ = await _collect(provider, [ChatMessage(Role.USER, "问题")], [_tool()])


def test_anthropic_tool_history_serializes_success_and_failure_results() -> None:
    call = ToolCall("tool-1", "read_file", '{"path":"note.txt"}')
    success = ToolExecutionResult("tool-1", "read_file", True, "读取成功", None)
    failed_call = ToolCall("tool-2", "read_file", '{"path":"missing.txt"}')
    failure = ToolExecutionResult(
        "tool-2",
        "read_file",
        False,
        "文件不存在",
        ToolErrorCode.NOT_FOUND,
    )
    serialized = AnthropicProvider._serialize_messages(
        [
            ChatMessage(Role.USER, "先读"),
            ChatMessage(Role.ASSISTANT, tool_call=call),
            ChatMessage(Role.TOOL, tool_result=success),
            ChatMessage(Role.ASSISTANT, tool_call=failed_call),
            ChatMessage(Role.TOOL, tool_result=failure),
        ]
    )

    first_call = serialized[1]["content"][0]  # type: ignore[index]
    first_result = serialized[2]["content"][0]  # type: ignore[index]
    failed_result = serialized[4]["content"][0]  # type: ignore[index]
    assert first_call["type"] == "tool_use"  # type: ignore[index]
    assert first_call["id"] == "tool-1"  # type: ignore[index]
    assert first_result["tool_use_id"] == "tool-1"  # type: ignore[index]
    assert first_result["is_error"] is False  # type: ignore[index]
    assert failed_result["tool_use_id"] == "tool-2"  # type: ignore[index]
    assert failed_result["is_error"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_current_anthropic_sdk_accumulates_input_json_delta() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkStream(
                [
                    _anthropic_event(
                        "message_start",
                        '{"type":"message_start","message":{"id":"m","type":"message",'
                        '"role":"assistant","content":[],"model":"claude-test",'
                        '"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":1}}}',
                    ),
                    _anthropic_event(
                        "content_block_start",
                        '{"type":"content_block_start","index":0,"content_block":'
                        '{"type":"tool_use","id":"tool-1","name":"read_file","input":{}}}',
                    ),
                    _anthropic_event(
                        "content_block_delta",
                        '{"type":"content_block_delta","index":0,"delta":'
                        '{"type":"input_json_delta","partial_json":"{\\"path\\":\\""}}',
                    ),
                    _anthropic_event(
                        "content_block_delta",
                        '{"type":"content_block_delta","index":0,"delta":'
                        '{"type":"input_json_delta","partial_json":"note.txt\\"}"}}',
                    ),
                    _anthropic_event(
                        "content_block_stop", '{"type":"content_block_stop","index":0}'
                    ),
                    _anthropic_event(
                        "message_delta",
                        '{"type":"message_delta","delta":{"stop_reason":"tool_use",'
                        '"stop_sequence":null},"usage":{"output_tokens":1}}',
                    ),
                    _anthropic_event("message_stop", '{"type":"message_stop"}'),
                ]
            ),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncAnthropic(
        api_key="test-key",
        base_url="https://mock.example",
        http_client=http_client,
        max_retries=0,
    )
    provider = AnthropicProvider(_config(), client=client)
    events = await _collect(provider, [ChatMessage(Role.USER, "读文件")], [_tool()])
    await provider.aclose()

    assert len(events) == 1
    assert isinstance(events[0], StreamCompleted)
    assert events[0].message.tool_call == ToolCall("tool-1", "read_file", '{"path":"note.txt"}')
