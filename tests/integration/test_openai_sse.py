from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

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
from okcode.providers.openai import OpenAIProvider
from okcode.tools.models import ToolDefinition, ToolErrorCode, ToolExecutionResult
from tests.helpers.sse import ChunkStream, sse_event


class FakeStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    def __aiter__(self) -> AsyncIterator[object]:
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCompletions:
    def __init__(self, streams: list[FakeStream | Exception]) -> None:
        self._streams = iter(streams)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> FakeStream:
        self.calls.append(kwargs)
        result = next(self._streams)
        if isinstance(result, Exception):
            raise result
        return result


class FakeOpenAIClient:
    def __init__(self, streams: list[FakeStream | Exception]) -> None:
        self.completions = FakeCompletions(streams)
        self.chat = SimpleNamespace(completions=self.completions)
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


def _config(*, thinking: bool = True, prompt_cache: bool = False) -> ProviderConfig:
    return ProviderConfig(
        name="deepseek",
        protocol=ProviderProtocol.OPENAI,
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        api_key="OKCODE_SECRET_DO_NOT_PRINT_7429",
        thinking=thinking,
        prompt_cache=prompt_cache,
    )


def _chunk(
    *,
    reasoning: str | None = None,
    content: str | None = None,
    finish: str | None = None,
    choices: bool = True,
    tool_calls: list[object] | None = None,
) -> object:
    if not choices:
        return SimpleNamespace(choices=[])
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(
                    reasoning_content=reasoning,
                    content=content,
                    refusal=None,
                    tool_calls=tool_calls,
                ),
                finish_reason=finish,
            )
        ]
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
    provider: OpenAIProvider,
    messages: list[ChatMessage],
    tools: list[ToolDefinition] | None = None,
) -> list[object]:
    return [event async for event in provider.stream(messages, tools or ())]


@pytest.mark.asyncio
async def test_thinking_request_and_reasoning_content_stream() -> None:
    stream = FakeStream(
        [
            _chunk(choices=False),
            _chunk(reasoning="先分析", content="答案"),
            _chunk(content="续写", finish="stop"),
        ]
    )
    client = FakeOpenAIClient([stream])
    provider = OpenAIProvider(_config(), client=client)

    events = await _collect(provider, [ChatMessage(Role.USER, "问题")])

    assert events == [
        ThinkingDelta("先分析"),
        TextDelta("答案"),
        TextDelta("续写"),
        StreamCompleted(ChatMessage(Role.ASSISTANT, "答案续写")),
    ]
    request = client.completions.calls[0]
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "reasoning_effort" not in request
    assert stream.closed is True


@pytest.mark.asyncio
async def test_no_thinking_extension_when_disabled() -> None:
    client = FakeOpenAIClient([FakeStream([_chunk(content="答案", finish="stop")])])
    provider = OpenAIProvider(_config(thinking=False), client=client)
    _ = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    assert "extra_body" not in client.completions.calls[0]


@pytest.mark.asyncio
async def test_history_only_serializes_formal_content() -> None:
    client = FakeOpenAIClient([FakeStream([_chunk(content="答案", finish="stop")])])
    provider = OpenAIProvider(_config(), client=client)
    messages = [
        ChatMessage(Role.USER, "问题"),
        ChatMessage(Role.ASSISTANT, "答案", provider_state={"reasoning_content": "不回传"}),
    ]
    _ = await _collect(provider, messages)
    assert client.completions.calls[0]["messages"] == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "答案"},
    ]


@pytest.mark.asyncio
async def test_openai_request_separates_system_prompt_and_cache_key() -> None:
    client = FakeOpenAIClient([FakeStream([_chunk(content="答案", finish="stop")])])
    provider = OpenAIProvider(_config(prompt_cache=True), client=client)
    request_data = _request([ChatMessage(Role.USER, "解释文件")], [_tool()], cache=True)

    _ = [event async for event in provider.stream(request_data)]

    request = client.completions.calls[0]
    messages = request["messages"]  # type: ignore[assignment]
    assert messages[0] == {"role": "system", "content": request_data.prompt.stable_system}
    assert messages[1]["role"] == "system"  # type: ignore[index]
    assert "<okcode-system-note" in messages[1]["content"]  # type: ignore[index]
    assert messages[2] == {"role": "user", "content": "解释文件"}  # type: ignore[index]
    assert request["prompt_cache_key"] == request_data.prompt.cache_key


@pytest.mark.asyncio
async def test_openai_without_prompt_cache_omits_cache_routing_fields() -> None:
    client = FakeOpenAIClient([FakeStream([_chunk(content="答案", finish="stop")])])
    provider = OpenAIProvider(_config(prompt_cache=False), client=client)

    _ = [
        event
        async for event in provider.stream(
            _request([ChatMessage(Role.USER, "解释文件")], [_tool()], cache=False)
        )
    ]

    request = client.completions.calls[0]
    assert "prompt_cache_key" not in request
    assert "prompt_cache_retention" not in request


@pytest.mark.asyncio
async def test_openai_cache_usage_is_reported_without_estimation() -> None:
    usage = SimpleNamespace(
        prompt_tokens=20,
        completion_tokens=3,
        total_tokens=23,
        prompt_tokens_details=SimpleNamespace(cached_tokens=12),
    )
    stream = FakeStream(
        [
            SimpleNamespace(choices=[], usage=usage),
            _chunk(content="答案", finish="stop"),
        ]
    )
    provider = OpenAIProvider(_config(), client=FakeOpenAIClient([stream]))

    events = [event async for event in provider.stream(_request([ChatMessage(Role.USER, "问题")]))]

    completed = events[-1]
    assert isinstance(completed, StreamCompleted)
    assert completed.usage.cache.read_tokens == 12
    assert completed.usage.cache.write_tokens is None
    assert completed.usage.cache.available is True


@pytest.mark.asyncio
async def test_openai_missing_cache_usage_is_marked_unavailable() -> None:
    usage = SimpleNamespace(
        prompt_tokens=20,
        completion_tokens=3,
        total_tokens=23,
        prompt_tokens_details=SimpleNamespace(),
    )
    stream = FakeStream(
        [
            SimpleNamespace(choices=[], usage=usage),
            _chunk(content="答案", finish="stop"),
        ]
    )
    provider = OpenAIProvider(_config(), client=FakeOpenAIClient([stream]))

    events = [event async for event in provider.stream(_request([ChatMessage(Role.USER, "问题")]))]

    completed = events[-1]
    assert isinstance(completed, StreamCompleted)
    assert completed.usage.cache.available is False
    assert completed.usage.cache.read_tokens is None
    assert completed.usage.cache.write_tokens is None


@pytest.mark.asyncio
async def test_missing_finish_reason_is_stream_error() -> None:
    client = FakeOpenAIClient([FakeStream([_chunk(content="答案")])])
    provider = OpenAIProvider(_config(), client=client)
    with pytest.raises(ProviderError) as raised:
        _ = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    assert raised.value.kind is ProviderErrorKind.STREAM


@pytest.mark.asyncio
async def test_empty_formal_answer_is_stream_error() -> None:
    client = FakeOpenAIClient([FakeStream([_chunk(reasoning="只有思考", finish="stop")])])
    provider = OpenAIProvider(_config(), client=client)
    with pytest.raises(ProviderError) as raised:
        _ = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    assert raised.value.kind is ProviderErrorKind.STREAM


@pytest.mark.asyncio
async def test_error_mapping_and_idempotent_close() -> None:
    error = RuntimeError("secret should never be printed")
    error.status_code = 401  # type: ignore[attr-defined]
    client = FakeOpenAIClient([error])
    provider = OpenAIProvider(_config(), client=client)
    with pytest.raises(ProviderError) as raised:
        _ = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    assert raised.value.kind is ProviderErrorKind.AUTHENTICATION
    assert "secret" not in raised.value.safe_message
    await provider.aclose()
    await provider.aclose()
    assert client.close_count == 1


@pytest.mark.asyncio
async def test_current_openai_sdk_parses_deepseek_reasoning_extension() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkStream(
                [
                    sse_event(
                        '{"id":"x","object":"chat.completion.chunk","created":1,'
                        '"model":"deepseek-v4-pro","choices":[{"index":0,'
                        '"delta":{"role":"assistant","reasoning_content":"分析"},'
                        '"finish_reason":null}]}'
                    ),
                    sse_event(
                        '{"id":"x","object":"chat.completion.chunk","created":1,'
                        '"model":"deepseek-v4-pro","choices":[{"index":0,'
                        '"delta":{"content":"答案"},"finish_reason":"stop"}]}'
                    ),
                    sse_event("[DONE]"),
                ]
            ),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://mock.example/v1",
        http_client=http_client,
        max_retries=0,
    )
    provider = OpenAIProvider(_config(), client=client)
    events = await _collect(provider, [ChatMessage(Role.USER, "问题")])
    await provider.aclose()

    assert events == [
        ThinkingDelta("分析"),
        TextDelta("答案"),
        StreamCompleted(ChatMessage(Role.ASSISTANT, "答案")),
    ]
    assert requests[0].url.path.endswith("/chat/completions")


@pytest.mark.asyncio
async def test_openai_tool_fragments_become_one_call_and_allows_parallel_calls() -> None:
    first_call = SimpleNamespace(
        index=0,
        id="call-1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"'),
    )
    second_call = SimpleNamespace(
        index=0,
        id=None,
        function=SimpleNamespace(name=None, arguments='note.txt"}'),
    )
    stream = FakeStream(
        [
            _chunk(tool_calls=[first_call]),
            _chunk(tool_calls=[second_call], finish="tool_calls"),
        ]
    )
    client = FakeOpenAIClient([stream])
    provider = OpenAIProvider(_config(), client=client)

    events = await _collect(provider, [ChatMessage(Role.USER, "读文件")], [_tool()])

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, StreamCompleted)
    assert completed.message.tool_call == ToolCall("call-1", "read_file", '{"path":"note.txt"}')
    request = client.completions.calls[0]
    assert "parallel_tool_calls" not in request
    assert request["stream_options"] == {"include_usage": True}
    assert request["tools"][0]["function"]["parameters"] == _tool().input_schema  # type: ignore[index]


@pytest.mark.asyncio
async def test_openai_returns_all_tool_calls_and_rejects_incomplete_tool_calls() -> None:
    multiple = FakeStream(
        [
            _chunk(
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="one",
                        function=SimpleNamespace(name="read_file", arguments="{}"),
                    ),
                    SimpleNamespace(
                        index=1,
                        id="two",
                        function=SimpleNamespace(name="read_file", arguments="{}"),
                    ),
                ],
                finish="tool_calls",
            )
        ]
    )
    incomplete = FakeStream(
        [
            _chunk(
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="one",
                        function=SimpleNamespace(name="read_file", arguments="{"),
                    )
                ],
                finish="tool_calls",
            )
        ]
    )
    provider = OpenAIProvider(_config(), client=FakeOpenAIClient([multiple, incomplete]))

    events = await _collect(provider, [ChatMessage(Role.USER, "问题")], [_tool()])
    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, StreamCompleted)
    assert completed.message.tool_call == ToolCall("one", "read_file", "{}")
    assert completed.message.tool_calls == (
        ToolCall("one", "read_file", "{}"),
        ToolCall("two", "read_file", "{}"),
    )

    with pytest.raises(ProviderError, match="合法 JSON"):
        _ = await _collect(provider, [ChatMessage(Role.USER, "问题")], [_tool()])


@pytest.mark.asyncio
async def test_openai_tool_history_serializes_success_and_failure_results() -> None:
    client = FakeOpenAIClient([FakeStream([_chunk(content="下一轮", finish="stop")])])
    provider = OpenAIProvider(_config(), client=client)
    call = ToolCall("call-1", "read_file", '{"path":"note.txt"}')
    success = ToolExecutionResult("call-1", "read_file", True, "读取成功", None)
    failure = ToolExecutionResult(
        "call-2",
        "read_file",
        False,
        "文件不存在",
        ToolErrorCode.NOT_FOUND,
    )
    failed_call = ToolCall("call-2", "read_file", '{"path":"missing.txt"}')
    messages = [
        ChatMessage(Role.USER, "先读"),
        ChatMessage(Role.ASSISTANT, tool_call=call),
        ChatMessage(Role.TOOL, tool_result=success),
        ChatMessage(Role.USER, "再读"),
        ChatMessage(Role.ASSISTANT, tool_call=failed_call),
        ChatMessage(Role.TOOL, tool_result=failure),
        ChatMessage(Role.USER, "继续"),
    ]

    _ = await _collect(provider, messages, [_tool()])

    serialized = client.completions.calls[0]["messages"]
    assert serialized[1]["tool_calls"][0]["id"] == "call-1"  # type: ignore[index]
    assert serialized[2]["role"] == "tool"  # type: ignore[index]
    assert serialized[2]["tool_call_id"] == "call-1"  # type: ignore[index]
    assert '"success":true' in serialized[2]["content"]  # type: ignore[index]
    assert serialized[5]["tool_call_id"] == "call-2"  # type: ignore[index]
    assert '"error_code":"not_found"' in serialized[5]["content"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_current_openai_sdk_accumulates_tool_call_fragments() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkStream(
                [
                    sse_event(
                        '{"id":"x","object":"chat.completion.chunk","created":1,'
                        '"model":"test","choices":[{"index":0,"delta":{"tool_calls":['
                        '{"index":0,"id":"call-1","type":"function","function":'
                        '{"name":"read_file","arguments":"{\\"path\\":\\""}}]},'
                        '"finish_reason":null}]}'
                    ),
                    sse_event(
                        '{"id":"x","object":"chat.completion.chunk","created":1,'
                        '"model":"test","choices":[{"index":0,"delta":{"tool_calls":['
                        '{"index":0,"function":{"arguments":"note.txt\\"}"}}]},'
                        '"finish_reason":"tool_calls"}]}'
                    ),
                    sse_event("[DONE]"),
                ]
            ),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://mock.example/v1",
        http_client=http_client,
        max_retries=0,
    )
    provider = OpenAIProvider(_config(), client=client)
    events = await _collect(provider, [ChatMessage(Role.USER, "读文件")], [_tool()])
    await provider.aclose()

    assert len(events) == 1
    assert isinstance(events[0], StreamCompleted)
    assert events[0].message.tool_call == ToolCall("call-1", "read_file", '{"path":"note.txt"}')
