"""Markdown 长期记忆笔记及受限索引的本地存储。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from okcode.memory.models import (
    MemoryAction,
    MemoryCategory,
    MemoryIndexEntry,
    MemoryOperation,
    MemoryPaths,
    MemoryScope,
    MemoryScopeUsage,
    MemorySnapshot,
    MemoryUpdate,
    validate_memory_name,
)

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

    def snapshot(self) -> MemorySnapshot:
        """返回当前两类记忆文件名及字节总量。"""

        return MemorySnapshot(
            project=self._scope_usage(MemoryScope.PROJECT),
            user=self._scope_usage(MemoryScope.USER),
        )

    def apply(self, update: MemoryUpdate) -> None:
        """校验完整更新后写入笔记和两份候选索引。"""

        operations = tuple(
            operation
            for operation in update.operations
            if operation.action is not MemoryAction.NOOP
        )
        self._validate_operations(operations)
        created = {
            (operation.scope, operation.name)
            for operation in operations
            if operation.action is MemoryAction.CREATE and operation.name is not None
        }
        self._validate_index(MemoryScope.USER, update.user_index, created)
        self._validate_index(MemoryScope.PROJECT, update.project_index, created)
        if not operations and not update.user_index and not update.project_index:
            return

        for operation in operations:
            assert operation.name is not None
            path = self.paths.note_for(operation.scope, operation.name)
            if operation.action is MemoryAction.CREATE:
                self._write_note(path, operation)
            else:
                self._append_note(path, operation)
        self._write_index(MemoryScope.USER, update.user_index)
        self._write_index(MemoryScope.PROJECT, update.project_index)

    def _validate_operations(self, operations: tuple[MemoryOperation, ...]) -> None:
        seen: set[tuple[MemoryScope, str]] = set()
        for operation in operations:
            if operation.action not in {MemoryAction.CREATE, MemoryAction.APPEND}:
                raise ValueError("记忆操作无效。")
            if operation.name is None:
                raise ValueError("记忆名称不能为空。")
            validate_memory_name(operation.name)
            if not operation.content.strip():
                raise ValueError("记忆笔记内容不能为空。")
            key = (operation.scope, operation.name)
            if key in seen:
                raise ValueError("同一批记忆更新不能重复操作同一笔记。")
            seen.add(key)
            path = self.paths.note_for(operation.scope, operation.name)
            if operation.action is MemoryAction.CREATE:
                if not operation.summary.strip():
                    raise ValueError("新建记忆笔记必须包含摘要。")
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
            validate_memory_name(entry.name)
            if entry.name in seen:
                raise ValueError("记忆索引不能重复引用同一笔记。")
            if not entry.summary.strip():
                raise ValueError("记忆索引摘要不能为空。")
            seen.add(entry.name)
            if (scope, entry.name) not in created and not self.paths.note_for(
                scope, entry.name
            ).is_file():
                raise ValueError("记忆索引引用了不存在的笔记。")
        self._check_index_limit(_render_index(entries))

    def _write_note(self, path: Path, operation: MemoryOperation) -> None:
        assert operation.name is not None
        timestamp = _as_utc(self._clock()).isoformat()
        frontmatter = _render_frontmatter(
            name=operation.name,
            scope=operation.scope.value,
            category=operation.category.value,
            summary=operation.summary,
            created_at=timestamp,
            updated_at=timestamp,
        )
        _atomic_write(path, frontmatter + "\n\n" + operation.content.strip() + "\n")

    def _append_note(self, path: Path, operation: MemoryOperation) -> None:
        existing = _read_text(path, "记忆笔记")
        metadata, note_body = _parse_note(existing, path)
        timestamp = _as_utc(self._clock()).isoformat()
        assert operation.name is not None
        stored_name = _metadata_string(metadata, "name") or _metadata_string(metadata, "id")
        if stored_name and stored_name != operation.name:
            raise ValueError("记忆文件 frontmatter 的 name 与文件名不一致。")
        category = _metadata_string(metadata, "category") or operation.category.value
        try:
            MemoryScope(metadata.get("scope", operation.scope.value))
            MemoryCategory(category)
        except ValueError as exc:
            raise ValueError("记忆笔记 frontmatter 的范围或分类无效。") from exc
        summary = (
            operation.summary.strip()
            or _metadata_string(metadata, "summary")
            or _metadata_string(metadata, "title")
        )
        if not summary:
            raise ValueError("记忆笔记 frontmatter 缺少摘要。")
        created_at = _metadata_string(metadata, "created_at") or timestamp
        frontmatter = _render_frontmatter(
            name=operation.name,
            scope=operation.scope.value,
            category=category,
            summary=summary,
            created_at=created_at,
            updated_at=timestamp,
        )
        body = note_body.strip()
        updated_body = f"{body}\n\n" if body else ""
        updated_body += f"## 更新 {timestamp}\n\n{operation.content.strip()}\n"
        _atomic_write(path, frontmatter + "\n\n" + updated_body)

    def _write_index(self, scope: MemoryScope, entries: tuple[MemoryIndexEntry, ...]) -> None:
        path = self.paths.index_for(scope)
        _atomic_write(path, _render_index(entries))

    def _read_index(self, scope: MemoryScope) -> str:
        for path in (self.paths.index_for(scope), self.paths.legacy_index_for(scope)):
            try:
                return _read_text(path, "记忆索引").strip()
            except FileNotFoundError:
                continue
        return ""

    def _scope_usage(self, scope: MemoryScope) -> MemoryScopeUsage:
        root = self.paths.root_for(scope)
        if not root.is_dir():
            return MemoryScopeUsage((), 0)
        files: list[str] = []
        total_bytes = 0
        for path in root.glob("*.md"):
            try:
                if not path.is_file():
                    continue
                total_bytes += path.stat().st_size
            except OSError:
                continue
            files.append(path.name)
        return MemoryScopeUsage(tuple(sorted(files)), total_bytes)

    def _check_index_limit(self, content: str) -> None:
        lines = len(content.splitlines())
        if lines > self.max_index_lines:
            raise ValueError("记忆索引超过最大行数。")
        if len(content.encode("utf-8")) > self.max_index_bytes:
            raise ValueError("记忆索引超过最大字节数。")


def _render_index(entries: Iterable[MemoryIndexEntry]) -> str:
    rows = []
    for entry in entries:
        name = _escape_link_text(entry.name)
        target = _escape_link_target(f"{entry.name}.md")
        rows.append(f"[{name}.md]({target}) | {_single_line(entry.summary)}")
    return "\n".join(rows) + ("\n" if rows else "")


def _render_frontmatter(
    *,
    name: str,
    scope: str,
    category: str,
    summary: str,
    created_at: str,
    updated_at: str,
) -> str:
    return "\n".join(
        (
            "---",
            f"name: {json.dumps(name, ensure_ascii=False)}",
            f"scope: {scope}",
            f"category: {category}",
            f"summary: {json.dumps(summary.strip(), ensure_ascii=False)}",
            f"created_at: {json.dumps(created_at, ensure_ascii=False)}",
            f"updated_at: {json.dumps(updated_at, ensure_ascii=False)}",
            "---",
        )
    )


def _parse_note(content: str, path: Path) -> tuple[dict[str, object], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"记忆笔记缺少 YAML frontmatter：{path}")
    end = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        raise ValueError(f"记忆笔记缺少 YAML frontmatter 结束标记：{path}")
    try:
        raw = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"记忆笔记 frontmatter YAML 无效：{path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"记忆笔记 frontmatter 必须是对象：{path}")
    return raw, "\n".join(lines[end + 1 :])


def _metadata_string(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    if isinstance(value, datetime):
        return value.isoformat()
    return value.strip() if isinstance(value, str) else ""


def _escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _escape_link_target(value: str) -> str:
    return value.replace("\\", "\\\\").replace(")", "\\)")


def _single_line(value: str) -> str:
    return " ".join(value.split())


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
