"""协议无关会话消息的 JSONL 编解码与工具配对校验。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from okcode.models import ChatMessage, Role, ToolCall
from okcode.sessions.models import StoredMessage
from okcode.tools.models import ToolErrorCode, ToolExecutionResult


def encode_record(timestamp: datetime, message: ChatMessage) -> str:
    """将一条消息编码为稳定的单行 JSON 记录。"""

    normalized = _as_utc(timestamp)
    payload = {
        "timestamp": normalized.isoformat(),
        "message": _encode_message(message),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_record(line: str) -> StoredMessage:
    """严格解析一行 JSONL，拒绝未知或缺失字段。"""

    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("会话 JSONL 行不是有效 JSON。") from exc
    record = _mapping(raw, "会话记录")
    _require_exact_fields(record, {"timestamp", "message"}, "会话记录")
    timestamp = _parse_timestamp(record["timestamp"])
    return StoredMessage(timestamp, _decode_message(record["message"]))


def complete_message_prefix(
    messages: Sequence[ChatMessage],
) -> tuple[tuple[ChatMessage, ...], bool]:
    """返回工具调用均有紧随匹配结果的最长历史前缀。"""

    accepted: list[ChatMessage] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role is Role.TOOL:
            return tuple(accepted), True
        if message.role is not Role.ASSISTANT or not message.tool_calls:
            accepted.append(message)
            index += 1
            continue
        if index + 1 >= len(messages):
            return tuple(accepted), True
        results_message = messages[index + 1]
        if not _matches_tool_results(message, results_message):
            return tuple(accepted), True
        accepted.extend((message, results_message))
        index += 2
    return tuple(accepted), False


def _encode_message(message: ChatMessage) -> dict[str, object]:
    payload: dict[str, object] = {
        "role": message.role.value,
        "content": message.content,
        "tool_calls": [_encode_tool_call(call) for call in message.tool_calls],
        "tool_results": [_encode_tool_result(result) for result in message.tool_results],
    }
    if message.provider_state is not None:
        _json_compatible(message.provider_state, "provider_state")
        payload["provider_state"] = message.provider_state
    return payload


def _decode_message(raw: object) -> ChatMessage:
    item = _mapping(raw, "消息")
    allowed = {"role", "content", "tool_calls", "tool_results", "provider_state"}
    _reject_unknown_fields(item, allowed, "消息")
    for field in ("role", "content", "tool_calls", "tool_results"):
        if field not in item:
            raise ValueError(f"消息缺少 {field} 字段。")
    try:
        role = Role(_string(item["role"], "消息.role"))
    except ValueError as exc:
        raise ValueError("消息.role 无效。") from exc
    content = _string(item["content"], "消息.content")
    tool_calls = tuple(
        _decode_tool_call(value) for value in _list(item["tool_calls"], "消息.tool_calls")
    )
    tool_results = tuple(
        _decode_tool_result(value) for value in _list(item["tool_results"], "消息.tool_results")
    )
    provider_state = item.get("provider_state")
    if "provider_state" in item:
        _json_compatible(provider_state, "消息.provider_state")
    try:
        return ChatMessage(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_results=tool_results,
            provider_state=provider_state,
        )
    except ValueError as exc:
        raise ValueError(f"消息字段组合无效：{exc}") from exc


def _encode_tool_call(call: ToolCall) -> dict[str, str]:
    return {"id": call.id, "name": call.name, "arguments_json": call.arguments_json}


def _decode_tool_call(raw: object) -> ToolCall:
    item = _mapping(raw, "工具调用")
    _require_exact_fields(item, {"id", "name", "arguments_json"}, "工具调用")
    return ToolCall(
        id=_string(item["id"], "工具调用.id"),
        name=_string(item["name"], "工具调用.name"),
        arguments_json=_string(item["arguments_json"], "工具调用.arguments_json"),
    )


def _encode_tool_result(result: ToolExecutionResult) -> dict[str, object]:
    return {
        "tool_call_id": result.tool_call_id,
        "tool_name": result.tool_name,
        "success": result.success,
        "content": result.content,
        "error_code": result.error_code.value if result.error_code else None,
        "data": dict(result.data),
        "truncated": result.truncated,
    }


def _decode_tool_result(raw: object) -> ToolExecutionResult:
    item = _mapping(raw, "工具结果")
    fields = {
        "tool_call_id",
        "tool_name",
        "success",
        "content",
        "error_code",
        "data",
        "truncated",
    }
    _require_exact_fields(item, fields, "工具结果")
    error_code: ToolErrorCode | None
    raw_error_code = item["error_code"]
    if raw_error_code is None:
        error_code = None
    else:
        try:
            error_code = ToolErrorCode(_string(raw_error_code, "工具结果.error_code"))
        except ValueError as exc:
            raise ValueError("工具结果.error_code 无效。") from exc
    success = item["success"]
    truncated = item["truncated"]
    if not isinstance(success, bool) or not isinstance(truncated, bool):
        raise ValueError("工具结果.success 和 truncated 必须是布尔值。")
    data = _mapping(item["data"], "工具结果.data")
    _json_compatible(data, "工具结果.data")
    return ToolExecutionResult(
        tool_call_id=_string(item["tool_call_id"], "工具结果.tool_call_id"),
        tool_name=_string(item["tool_name"], "工具结果.tool_name"),
        success=success,
        content=_string(item["content"], "工具结果.content"),
        error_code=error_code,
        data=data,
        truncated=truncated,
    )


def _matches_tool_results(calls_message: ChatMessage, results_message: ChatMessage) -> bool:
    if results_message.role is not Role.TOOL:
        return False
    calls = {call.id: call for call in calls_message.tool_calls}
    if len(calls) != len(calls_message.tool_calls):
        return False
    results = {result.tool_call_id: result for result in results_message.tool_results}
    if len(results) != len(results_message.tool_results) or set(results) != set(calls):
        return False
    return all(results[call_id].tool_name == call.name for call_id, call in calls.items())


def _parse_timestamp(raw: object) -> datetime:
    value = _string(raw, "会话记录.timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("会话记录.timestamp 无效。") from exc
    if parsed.tzinfo is None:
        raise ValueError("会话记录.timestamp 必须包含时区。")
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("会话时间必须包含时区。")
    return value.astimezone(UTC)


def _mapping(raw: object, location: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{location} 必须是对象。")
    return raw


def _list(raw: object, location: str) -> list[object]:
    if not isinstance(raw, list):
        raise ValueError(f"{location} 必须是列表。")
    return raw


def _string(raw: object, location: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{location} 必须是字符串。")
    return raw


def _require_exact_fields(item: Mapping[str, object], fields: set[str], location: str) -> None:
    _reject_unknown_fields(item, fields, location)
    if set(item) != fields:
        missing = fields - set(item)
        raise ValueError(f"{location} 缺少字段：{', '.join(sorted(missing))}")


def _reject_unknown_fields(item: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(f"{location} 包含未知字段：{', '.join(sorted(unknown))}")


def _json_compatible(value: object, location: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{location} 必须是 JSON 值。") from exc
