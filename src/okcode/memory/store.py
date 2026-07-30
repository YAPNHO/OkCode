"""Markdown 长期记忆笔记及受限索引的本地存储。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from okcode.memory.models import (
    MemoryAction,
    MemoryIndexEntry,
    MemoryOperation,
    MemoryPaths,
    MemoryScope,
    MemoryUpdate,
)

_NOTE_REF_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_READ_ENCODINGS = ("utf-8", "cp936")


class MemoryStore:
    """读取双范围索引，并原子更新 Markdown 笔记和索引。"""

    def __init__(
        self,
        paths: MemoryPaths,
        *,
        max_index_lines: int = 200,
        max_index_bytes: int = 25_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_index_lines < 1 or max_index_bytes < 1:
            raise ValueError("记忆索引上限必须为正数。")
        self.paths = paths
        self.max_index_lines = max_index_lines
        self.max_index_bytes = max_index_bytes
        self._clock = clock or _utc_now

    def read_context(self) -> str:
        """读取当前两份索引，供每次普通模型请求注入。"""

        sections = []
        user_index, project_index = self.read_indexes()
        for title, content in (
            ("用户级长期记忆", user_index),
            ("项目级长期记忆", project_index),
        ):
            if content:
                sections.append(f"## {title}\n{content}")
        return "\n\n".join(sections)

    def read_indexes(self) -> tuple[str, str]:
        """分别读取用户级和项目级的当前索引。"""

        return self._read_index(MemoryScope.USER), self._read_index(MemoryScope.PROJECT)

    def apply(self, update: MemoryUpdate) -> None:
        """校验完整更新后写入笔记和两份候选索引。"""

        operations = tuple(
            operation
            for operation in update.operations
            if operation.action is not MemoryAction.NOOP
        )
        self._validate_operations(operations)
        created = {
            (operation.scope, operation.note_ref)
            for operation in operations
            if operation.action is MemoryAction.CREATE and operation.note_ref is not None
        }
        self._validate_index(MemoryScope.USER, update.user_index, created)
        self._validate_index(MemoryScope.PROJECT, update.project_index, created)
        if not operations and not update.user_index and not update.project_index:
            return

        for operation in operations:
            assert operation.note_ref is not None
            path = self.paths.note_for(operation.scope, operation.note_ref)
            if operation.action is MemoryAction.CREATE:
                self._write_note(path, operation)
            else:
                self._append_note(path, operation.content)
        self._write_index(MemoryScope.USER, update.user_index)
        self._write_index(MemoryScope.PROJECT, update.project_index)

    def _validate_operations(self, operations: tuple[MemoryOperation, ...]) -> None:
        seen: set[tuple[MemoryScope, str]] = set()
        for operation in operations:
            if operation.action not in {MemoryAction.CREATE, MemoryAction.APPEND}:
                raise ValueError("记忆操作无效。")
            if operation.note_ref is None or not _NOTE_REF_PATTERN.fullmatch(operation.note_ref):
                raise ValueError("记忆笔记标识无效。")
            if not operation.content.strip():
                raise ValueError("记忆笔记内容不能为空。")
            key = (operation.scope, operation.note_ref)
            if key in seen:
                raise ValueError("同一批记忆更新不能重复操作同一笔记。")
            seen.add(key)
            path = self.paths.note_for(operation.scope, operation.note_ref)
            if operation.action is MemoryAction.CREATE:
                if not operation.title.strip():
                    raise ValueError("新建记忆笔记必须包含标题。")
                if path.exists():
                    raise ValueError("新建记忆笔记已存在。")
            elif not path.is_file():
                raise ValueError("追加的记忆笔记不存在。")

    def _validate_index(
        self,
        scope: MemoryScope,
        entries: tuple[MemoryIndexEntry, ...],
        created: set[tuple[MemoryScope, str]],
    ) -> None:
        seen: set[str] = set()
        for entry in entries:
            if not _NOTE_REF_PATTERN.fullmatch(entry.note_ref):
                raise ValueError("记忆索引笔记标识无效。")
            if entry.note_ref in seen:
                raise ValueError("记忆索引不能重复引用同一笔记。")
            if not entry.summary.strip():
                raise ValueError("记忆索引摘要不能为空。")
            seen.add(entry.note_ref)
            if (scope, entry.note_ref) not in created and not self.paths.note_for(
                scope, entry.note_ref
            ).is_file():
                raise ValueError("记忆索引引用了不存在的笔记。")
        self._check_index_limit(_render_index(entries))

    def _write_note(self, path: Path, operation: MemoryOperation) -> None:
        timestamp = _as_utc(self._clock()).isoformat()
        frontmatter = "\n".join(
            (
                "---",
                f"id: {operation.note_ref}",
                f"scope: {operation.scope.value}",
                f"category: {operation.category.value}",
                f"created_at: {timestamp}",
                f"updated_at: {timestamp}",
                f"title: {json.dumps(operation.title, ensure_ascii=False)}",
                "---",
            )
        )
        _atomic_write(path, frontmatter + "\n\n" + operation.content.strip() + "\n")

    def _append_note(self, path: Path, content: str) -> None:
        existing = _read_text(path, "记忆笔记")
        timestamp = _as_utc(self._clock()).isoformat()
        updated = _replace_updated_at(existing, timestamp)
        body = content.strip()
        _atomic_write(path, updated.rstrip() + f"\n\n## 更新 {timestamp}\n\n{body}\n")

    def _write_index(self, scope: MemoryScope, entries: tuple[MemoryIndexEntry, ...]) -> None:
        path = self.paths.index_for(scope)
        _atomic_write(path, _render_index(entries))

    def _read_index(self, scope: MemoryScope) -> str:
        path = self.paths.index_for(scope)
        try:
            return _read_text(path, "记忆索引").strip()
        except FileNotFoundError:
            return ""

    def _check_index_limit(self, content: str) -> None:
        lines = content.count("\n") + 1 if content else 0
        if lines > self.max_index_lines:
            raise ValueError("记忆索引超过最大行数。")
        if len(content.encode("utf-8")) > self.max_index_bytes:
            raise ValueError("记忆索引超过最大字节数。")


def _render_index(entries: Iterable[MemoryIndexEntry]) -> str:
    rows = ["# 长期记忆索引"]
    rows.extend(
        "- "
        f"[{entry.note_ref}]({entry.note_ref}.md) | {entry.category.value} | "
        f"{entry.summary.strip()}"
        for entry in entries
    )
    return "\n".join(rows) + "\n"


def _replace_updated_at(content: str, timestamp: str) -> str:
    pattern = re.compile(r"^updated_at: .*?$", re.MULTILINE)
    if not pattern.search(content):
        raise ValueError("记忆笔记缺少 updated_at frontmatter。")
    return pattern.sub(f"updated_at: {timestamp}", content, count=1)


def _read_text(path: Path, description: str) -> str:
    last_decode_error: UnicodeDecodeError | None = None
    for encoding in _READ_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_decode_error = exc
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ValueError(f"无法读取{description}：{path}") from exc
    raise ValueError(f"{description}不是 UTF-8 或 Windows 中文编码：{path}") from last_decode_error


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("记忆时间必须包含时区。")
    return value.astimezone(UTC)
