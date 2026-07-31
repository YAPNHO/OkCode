"""Anthropic Messages 与 extended thinking 适配器。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from copy import deepcopy
from typing import Any

from anthropic import AsyncAnthropic

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    ChatMessage,
    ProviderConfig,
    ProviderRequest,
    Role,
    StreamCompleted,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    ToolCall,
)
from okcode.prompt.builder import PromptBundle
from okcode.prompt.cache import PromptCachePolicy, PromptCacheUsage
from okcode.tools.models import ToolDefinition

_MAX_TOKENS = 4096
_THINKING_BUDGET_TOKENS = 1024
_UNKNOWN_TOOL_INDEX = -1


class AnthropicProvider:
    """将 Anthropic Messages 流转换为统一事件。"""

    def __init__(self, config: ProviderConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client or AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
        )
        self._closed = False

    def stream(
        self,
        request: ProviderRequest | Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> AsyncIterator[StreamEvent]:
        return self._stream(_coerce_request(request, tools))

    async def _stream(
        self,
        request_data: ProviderRequest,
    ) -> AsyncIterator[StreamEvent]:
        started = False
        saw_message_stop = False
        try:
            request: dict[str, Any] = {
                "model": request_data.model_override or self._config.model,
                "max_tokens": _MAX_TOKENS,
                "messages": self._serialize_messages(request_data.messages),
            }
            system = _serialize_system(request_data.prompt, request_data.cache)
            if system:
                request["system"] = system
            if self._config.thinking:
                request["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": _THINKING_BUDGET_TOKENS,
                }
            if request_data.tools:
                request["tools"] = [
                    _serialize_tool_definition(
                        tool,
                        cache_control=request_data.cache.enabled
                        and index == len(request_data.tools) - 1,
                    )
                    for index, tool in enumerate(request_data.tools)
                ]
                request["tool_choice"] = {"type": "auto"}

            tool_input_parts: dict[int, list[str]] = {}
            usage = TokenUsage.unavailable()
            async with self._client.messages.stream(**request) as stream:
                async for event in stream:
                    started = True
                    usage = _usage_from_event(event, usage)
                    event_type = getattr(event, "type", "")
                    if event_type == "thinking":
                        thinking = getattr(event, "thinking", "")
                        if thinking:
                            yield ThinkingDelta(thinking)
                    elif event_type == "text":
                        text = getattr(event, "text", "")
                        if text:
                            yield TextDelta(text)
                    elif event_type == "input_json":
                        partial_json = getattr(event, "partial_json", "")
                        if not isinstance(partial_json, str):
                            raise ProviderError(
                                ProviderErrorKind.STREAM,
                                "工具调用参数分片格式无效。",
                            )
                        index = getattr(event, "index", _UNKNOWN_TOOL_INDEX)
                        if not isinstance(index, int):
                            index = _UNKNOWN_TOOL_INDEX
                        tool_input_parts.setdefault(index, []).append(partial_json)
                    elif event_type == "message_stop":
                        saw_message_stop = True

                final_message = await stream.get_final_message()
                usage = _usage_from_event(final_message, usage)

            answer = _extract_text(final_message)
            if not saw_message_stop or not getattr(final_message, "stop_reason", None):
                raise ProviderError(
                    ProviderErrorKind.STREAM,
                    "模型流在完成前意外结束，请重试。",
                )
            tool_blocks = _extract_tool_blocks(final_message)
            if tool_blocks and getattr(final_message, "stop_reason", None) != "tool_use":
                raise ProviderError(ProviderErrorKind.STREAM, "工具调用的停止原因不正确。")
            if tool_blocks:
                calls = tuple(
                    _build_tool_call(
                        tool_block,
                        tool_input_parts.get(
                            tool_index,
                            tool_input_parts.get(_UNKNOWN_TOOL_INDEX, []),
                        ),
                    )
                    for tool_index, tool_block in tool_blocks
                )
                state = tuple(_serialize_block(block) for block in final_message.content)
                yield StreamCompleted(
                    ChatMessage(
                        role=Role.ASSISTANT,
                        content=answer,
                        tool_calls=calls,
                        provider_state=state,
                    ),
                    usage=usage,
                )
                return
            if not answer.strip():
                raise ProviderError(
                    ProviderErrorKind.STREAM,
                    "模型未返回可显示的正式回答。",
                )
            state = tuple(_serialize_block(block) for block in final_message.content)
            yield StreamCompleted(
                ChatMessage(role=Role.ASSISTANT, content=answer, provider_state=state),
                usage=usage,
            )
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise _provider_error(exc, started=started) from exc

    async def aclose(self) -> None:
        """关闭底层异步 HTTP 客户端。"""

        if self._closed:
            return
        self._closed = True
        await self._client.close()

    @staticmethod
    def _serialize_messages(messages: Sequence[ChatMessage]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for message in messages:
            if message.role is Role.USER:
                result.append({"role": "user", "content": message.content})
            elif message.role is Role.ASSISTANT:
                content: object = message.content
                if message.provider_state is not None:
                    content = deepcopy(message.provider_state)
                elif message.tool_calls:
                    content = [
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": json.loads(call.arguments_json),
                        }
                        for call in message.tool_calls
                    ]
                result.append({"role": "assistant", "content": content})
            elif message.role is Role.TOOL and message.tool_results:
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_result.tool_call_id,
                                "content": tool_result.to_json(),
                                "is_error": not tool_result.success,
                            }
                            for tool_result in message.tool_results
                        ],
                    }
                )
            else:
                raise ValueError("无法序列化无效的 Anthropic 会话消息。")
        return result


def _extract_text(message: Any) -> str:
    return "".join(
        block.text
        for block in getattr(message, "content", ())
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    )


def _extract_tool_blocks(message: Any) -> list[tuple[int, Any]]:
    return [
        (index, block)
        for index, block in enumerate(getattr(message, "content", ()))
        if getattr(block, "type", None) == "tool_use"
    ]


def _serialize_tool_definition(
    tool: ToolDefinition,
    *,
    cache_control: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema),
    }
    if cache_control:
        result["cache_control"] = {"type": "ephemeral"}
    return result


def _serialize_system(
    prompt: PromptBundle,
    cache: PromptCachePolicy,
) -> list[dict[str, object]]:
    """将稳定提示和动态补充分成 Anthropic system blocks。"""

    result: list[dict[str, object]] = []
    if prompt.stable_system:
        stable: dict[str, object] = {"type": "text", "text": prompt.stable_system}
        if cache.enabled:
            stable["cache_control"] = {"type": "ephemeral", "ttl": cache.ttl}
        result.append(stable)
    result.extend(
        {"type": "text", "text": instruction.render()} for instruction in prompt.dynamic_system
    )
    return result


def _coerce_request(
    request: ProviderRequest | Sequence[ChatMessage],
    tools: Sequence[ToolDefinition],
) -> ProviderRequest:
    """兼容现有直接传消息和工具的 Provider 集成测试。"""

    if isinstance(request, ProviderRequest):
        return request
    return ProviderRequest(
        messages=tuple(request),
        tools=tuple(tools),
        prompt=PromptBundle("", (), "", ""),
        cache=PromptCachePolicy(),
    )


def _build_tool_call(block: Any, input_parts: list[str]) -> ToolCall:
    call_id = getattr(block, "id", None)
    name = getattr(block, "name", None)
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
        raise ProviderError(ProviderErrorKind.STREAM, "工具调用缺少 ID 或名称。")

    arguments_json = "".join(input_parts)
    if arguments_json:
        try:
            parsed = json.loads(arguments_json)
        except json.JSONDecodeError as exc:
            raise ProviderError(ProviderErrorKind.STREAM, "工具调用参数不是合法 JSON。") from exc
    else:
        parsed = getattr(block, "input", None)
        arguments_json = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(parsed, dict):
        raise ProviderError(ProviderErrorKind.STREAM, "工具调用参数必须是 JSON 对象。")
    return ToolCall(id=call_id, name=name, arguments_json=arguments_json)


def _serialize_block(block: Any) -> dict[str, object]:
    if hasattr(block, "model_dump"):
        return deepcopy(block.model_dump(exclude_none=True))
    if hasattr(block, "to_dict"):
        return deepcopy(block.to_dict())
    if isinstance(block, dict):
        return deepcopy(block)
    raise ProviderError(ProviderErrorKind.STREAM, "无法保存模型返回的对话状态。")


def _usage_from_event(event: object, fallback: TokenUsage) -> TokenUsage:
    usage = getattr(event, "usage", None)
    message = getattr(event, "message", None)
    if usage is None and message is not None:
        usage = getattr(message, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    read_tokens = getattr(usage, "cache_read_input_tokens", None)
    write_tokens = getattr(usage, "cache_creation_input_tokens", None)
    cache = (
        PromptCacheUsage(
            read_tokens=read_tokens if isinstance(read_tokens, int) else None,
            write_tokens=write_tokens if isinstance(write_tokens, int) else None,
            available=True,
        )
        if isinstance(read_tokens, int) or isinstance(write_tokens, int)
        else fallback.cache
    )
    if input_tokens is None and output_tokens is None:
        if cache is fallback.cache:
            return fallback
        return TokenUsage(
            input_tokens=fallback.input_tokens,
            output_tokens=fallback.output_tokens,
            total_tokens=fallback.total_tokens,
            available=fallback.available,
            cache=cache,
        )
    return TokenUsage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else fallback.input_tokens,
        output_tokens=output_tokens if isinstance(output_tokens, int) else fallback.output_tokens,
        total_tokens=None,
        available=True,
        cache=cache,
    )


def _provider_error(exc: Exception, *, started: bool) -> ProviderError:
    if started:
        return ProviderError(ProviderErrorKind.STREAM, "流式连接中断，请重试。")

    status_code = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    kind_by_status = {
        400: ProviderErrorKind.BAD_REQUEST,
        401: ProviderErrorKind.AUTHENTICATION,
        403: ProviderErrorKind.PERMISSION,
        408: ProviderErrorKind.TIMEOUT,
        429: ProviderErrorKind.RATE_LIMIT,
    }
    if status_code in kind_by_status:
        kind = kind_by_status[status_code]
    elif isinstance(status_code, int) and status_code >= 500:
        kind = ProviderErrorKind.SERVER
    elif type(exc).__name__ == "APITimeoutError":
        kind = ProviderErrorKind.TIMEOUT
    elif type(exc).__name__ == "APIConnectionError":
        kind = ProviderErrorKind.CONNECTION
    else:
        kind = ProviderErrorKind.BAD_REQUEST

    messages = {
        ProviderErrorKind.AUTHENTICATION: "认证失败，请检查 API Key。",
        ProviderErrorKind.PERMISSION: "没有访问该模型或服务的权限。",
        ProviderErrorKind.CONNECTION: "无法连接模型服务，请检查网络和地址。",
        ProviderErrorKind.TIMEOUT: "模型服务响应超时，请稍后重试。",
        ProviderErrorKind.RATE_LIMIT: "请求过于频繁，请稍后重试。",
        ProviderErrorKind.BAD_REQUEST: "模型服务拒绝了请求，请检查模型和配置。",
        ProviderErrorKind.SERVER: "模型服务暂时异常，请稍后重试。",
        ProviderErrorKind.STREAM: "流式连接中断，请重试。",
    }
    return ProviderError(kind, messages[kind], status_code=status_code, request_id=request_id)
