"""长期记忆的无工具 LLM 请求和严格 JSON 响应解析。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from okcode.memory.models import (
    MemoryAction,
    MemoryCategory,
    MemoryIndexEntry,
    MemoryJob,
    MemoryOperation,
    MemoryScope,
    MemoryUpdate,
)
from okcode.models import ChatMessage, ProviderRequest, Role
from okcode.prompt import PromptBundle, PromptCachePolicy

_MEMORY_SYSTEM_PROMPT = """你是 OkCode 的内部长期记忆整理器。
此请求绝对禁止调用工具。你只能根据输入的本轮消息和既有索引，提炼以下四类可复用信息：
- preference：用户稳定偏好；
- correction：用户对助手行为的纠正；
- project_knowledge：当前项目的稳定事实；
- reference：未来可复用的参考资料。

请由你判断内容是否重复：没有值得保存的内容时使用 action 为 noop。已有笔记需要补充时使用 append，
新事实使用 create。不得臆造未在输入中出现的事实。

最终只能输出一个 JSON 对象，不能使用 Markdown 代码块或输出其他文本。
对象必须且只能包含 operations、user_index、project_index 三个字段。
operations 的每项只能包含 scope、category、action、note_ref、title、content；
user_index 和 project_index 的每项只能包含 note_ref、category、summary。
两份索引都必须是更新后的完整索引。"""


class MemoryRequestFactory:
    """构造和解析长期记忆内部请求。"""

    def build(self, job: MemoryJob, user_index: str, project_index: str) -> ProviderRequest:
        """构造不带工具且禁用缓存的记忆请求。"""

        prompt = PromptBundle(
            stable_system=_MEMORY_SYSTEM_PROMPT,
            dynamic_system=(),
            debug_full_prompt=_MEMORY_SYSTEM_PROMPT,
            cache_key="memory-update",
        )
        return ProviderRequest(
            messages=(
                ChatMessage(Role.USER, _transcript(job.messages, user_index, project_index)),
            ),
            tools=(),
            prompt=prompt,
            cache=PromptCachePolicy(enabled=False),
        )

    def parse(self, response_text: str) -> MemoryUpdate:
        """严格解析模型返回的唯一 JSON 对象。"""

        try:
            raw = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ValueError("记忆模型响应必须是 JSON 对象。") from exc
        item = _mapping(raw, "记忆响应")
        _require_exact_fields(item, {"operations", "user_index", "project_index"}, "记忆响应")
        return MemoryUpdate(
            operations=tuple(
                _parse_operation(value) for value in _list(item["operations"], "operations")
            ),
            user_index=tuple(
                _parse_index_entry(value) for value in _list(item["user_index"], "user_index")
            ),
            project_index=tuple(
                _parse_index_entry(value) for value in _list(item["project_index"], "project_index")
            ),
        )


def _transcript(messages: Sequence[ChatMessage], user_index: str, project_index: str) -> str:
    sections = [
        "[用户级既有索引]\n" + (user_index or "（空）"),
        "[项目级既有索引]\n" + (project_index or "（空）"),
        "[本轮消息]",
    ]
    for message in messages:
        rows = [f"<{message.role.value}>"]
        if message.content:
            rows.append(message.content)
        for call in message.tool_calls:
            rows.append(f"工具调用 id={call.id} 名称={call.name} 参数={call.arguments_json}")
        for result in message.tool_results:
            rows.append("工具结果 " + result.to_json())
        rows.append(f"</{message.role.value}>")
        sections.append("\n".join(rows))
    return "\n\n".join(sections)


def _parse_operation(raw: object) -> MemoryOperation:
    item = _mapping(raw, "operations 项")
    fields = {"scope", "category", "action", "note_ref", "title", "content"}
    _require_exact_fields(item, fields, "operations 项")
    try:
        scope = MemoryScope(_string(item["scope"], "operations.scope"))
        category = MemoryCategory(_string(item["category"], "operations.category"))
        action = MemoryAction(_string(item["action"], "operations.action"))
    except ValueError as exc:
        raise ValueError("operations 包含无效的范围、分类或操作。") from exc
    note_ref = item["note_ref"]
    if note_ref is not None and not isinstance(note_ref, str):
        raise ValueError("operations.note_ref 必须是字符串或 null。")
    return MemoryOperation(
        scope=scope,
        category=category,
        action=action,
        note_ref=note_ref,
        title=_string(item["title"], "operations.title"),
        content=_string(item["content"], "operations.content"),
    )


def _parse_index_entry(raw: object) -> MemoryIndexEntry:
    item = _mapping(raw, "索引项")
    _require_exact_fields(item, {"note_ref", "category", "summary"}, "索引项")
    try:
        category = MemoryCategory(_string(item["category"], "索引项.category"))
    except ValueError as exc:
        raise ValueError("索引项.category 无效。") from exc
    return MemoryIndexEntry(
        note_ref=_string(item["note_ref"], "索引项.note_ref"),
        category=category,
        summary=_string(item["summary"], "索引项.summary"),
    )


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
    unknown = set(item) - fields
    if unknown:
        raise ValueError(f"{location} 包含未知字段：{', '.join(sorted(unknown))}")
    if set(item) != fields:
        missing = fields - set(item)
        raise ValueError(f"{location} 缺少字段：{', '.join(sorted(missing))}")
