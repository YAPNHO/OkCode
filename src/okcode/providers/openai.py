"""OpenAI Chat Completions 与兼容服务适配器。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    ChatMessage,
    ProviderConfig,
    Role,
    StreamCompleted,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    ToolCall,
)
from okcode.tools.models import ToolDefinition


class OpenAIProvider:
    """将 OpenAI Chat Completions 流转换为统一事件。"""

    def __init__(self, config: ProviderConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client or AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=0,
        )
        self._closed = False

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> AsyncIterator[StreamEvent]:
        return self._stream(messages, tools)

    async def _stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]:
        started = False
        saw_finish_reason = False
        answer_parts: list[str] = []
        tool_calls: dict[int, _OpenAIToolCallParts] = {}
        try:
            request: dict[str, Any] = {
                "model": self._config.model,
                "messages": self._serialize_messages(messages),
                "stream": True,
            }
            if self._config.thinking:
                request["extra_body"] = {"thinking": {"type": "enabled"}}
            if tools:
                request["tools"] = [_serialize_tool_definition(tool) for tool in tools]
            request["stream_options"] = {"include_usage": True}

            stream = await self._client.chat.completions.create(**request)
            usage = TokenUsage.unavailable()
            async with stream:
                async for chunk in stream:
                    started = True
                    usage = _usage_from_object(getattr(chunk, "usage", None), usage)
                    for choice in getattr(chunk, "choices", ()) or ():
                        if getattr(choice, "index", 0) != 0:
                            continue
                        delta = getattr(choice, "delta", None)
                        if delta is not None:
                            reasoning = _extension_value(delta, "reasoning_content")
                            if isinstance(reasoning, str) and reasoning:
                                yield ThinkingDelta(reasoning)

                            content = getattr(delta, "content", None)
                            refusal = getattr(delta, "refusal", None)
                            text = content if isinstance(content, str) and content else refusal
                            if isinstance(text, str) and text:
                                answer_parts.append(text)
                                yield TextDelta(text)

                            for raw_call in getattr(delta, "tool_calls", ()) or ():
                                _accumulate_tool_call(tool_calls, raw_call)

                        if getattr(choice, "finish_reason", None) is not None:
                            saw_finish_reason = True

            if not saw_finish_reason:
                raise ProviderError(
                    ProviderErrorKind.STREAM,
                    "模型流在完成前意外结束，请重试。",
                )
            answer = "".join(answer_parts)
            if tool_calls:
                calls = tuple(_build_tool_call(tool_calls[index]) for index in sorted(tool_calls))
                yield StreamCompleted(
                    ChatMessage(role=Role.ASSISTANT, content=answer, tool_calls=calls),
                    usage=usage,
                )
                return
            if not answer.strip():
                raise ProviderError(
                    ProviderErrorKind.STREAM,
                    "模型未返回可显示的正式回答。",
                )
            yield StreamCompleted(ChatMessage(role=Role.ASSISTANT, content=answer), usage=usage)
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
    def _serialize_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            if message.role is Role.USER:
                result.append({"role": "user", "content": message.content})
            elif message.role is Role.ASSISTANT:
                payload: dict[str, Any] = {"role": "assistant", "content": message.content or None}
                if message.tool_calls:
                    payload["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments_json,
                            },
                        }
                        for call in message.tool_calls
                    ]
                result.append(payload)
            elif message.role is Role.TOOL and message.tool_results:
                for tool_result in message.tool_results:
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_result.tool_call_id,
                            "content": tool_result.to_json(),
                        }
                    )
            else:
                raise ValueError("无法序列化无效的 OpenAI 会话消息。")
        return result


@dataclass
class _OpenAIToolCallParts:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: list[str] = field(default_factory=list)


def _serialize_tool_definition(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _accumulate_tool_call(states: dict[int, _OpenAIToolCallParts], raw_call: object) -> None:
    index = getattr(raw_call, "index", None)
    if not isinstance(index, int):
        raise ProviderError(ProviderErrorKind.STREAM, "工具调用缺少有效索引。")
    state = states.setdefault(index, _OpenAIToolCallParts(index=index))
    call_id = getattr(raw_call, "id", None)
    if isinstance(call_id, str) and call_id:
        if state.call_id is not None and state.call_id != call_id:
            raise ProviderError(ProviderErrorKind.STREAM, "工具调用 ID 在流中不一致。")
        state.call_id = call_id

    function = getattr(raw_call, "function", None)
    name = getattr(function, "name", None)
    if isinstance(name, str) and name:
        if state.name is not None and state.name != name:
            raise ProviderError(ProviderErrorKind.STREAM, "工具调用名称在流中不一致。")
        state.name = name
    arguments = getattr(function, "arguments", None)
    if isinstance(arguments, str):
        state.arguments.append(arguments)


def _build_tool_call(parts: _OpenAIToolCallParts) -> ToolCall:
    arguments_json = "".join(parts.arguments)
    if not parts.call_id or not parts.name or not arguments_json:
        raise ProviderError(ProviderErrorKind.STREAM, "工具调用缺少 ID、名称或参数。")
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise ProviderError(ProviderErrorKind.STREAM, "工具调用参数不是合法 JSON。") from exc
    if not isinstance(arguments, dict):
        raise ProviderError(ProviderErrorKind.STREAM, "工具调用参数必须是 JSON 对象。")
    return ToolCall(id=parts.call_id, name=parts.name, arguments_json=arguments_json)


def _usage_from_object(value: object | None, fallback: TokenUsage) -> TokenUsage:
    if value is None:
        return fallback
    input_tokens = getattr(value, "prompt_tokens", None)
    output_tokens = getattr(value, "completion_tokens", None)
    total_tokens = getattr(value, "total_tokens", None)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return fallback
    return TokenUsage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else None,
        output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        total_tokens=total_tokens if isinstance(total_tokens, int) else None,
        available=True,
    )


def _extension_value(value: object, name: str) -> object | None:
    """读取 SDK 未声明但兼容服务可能返回的扩展字段。"""

    direct = getattr(value, name, None)
    if direct is not None:
        return direct
    extra = getattr(value, "model_extra", None)
    if isinstance(extra, dict):
        return extra.get(name)
    return None


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
