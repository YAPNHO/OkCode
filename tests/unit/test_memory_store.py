from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from okcode.memory.models import (
    MemoryAction,
    MemoryCategory,
    MemoryIndexEntry,
    MemoryOperation,
    MemoryPaths,
    MemoryScope,
    MemoryUpdate,
)
from okcode.memory.store import MemoryStore


def _now() -> datetime:
    return datetime(2026, 7, 30, 10, 0, tzinfo=UTC)


def _paths(tmp_path: Path) -> MemoryPaths:
    return MemoryPaths(tmp_path / "project-memory", tmp_path / "user-memory")


def _store(tmp_path: Path, **kwargs: object) -> MemoryStore:
    return MemoryStore(_paths(tmp_path), clock=_now, **kwargs)


def _entry(note_ref: str, category: MemoryCategory, summary: str = "摘要") -> MemoryIndexEntry:
    return MemoryIndexEntry(note_ref, category, summary)


def _create(
    scope: MemoryScope,
    category: MemoryCategory,
    note_ref: str,
    content: str = "笔记正文",
) -> MemoryOperation:
    return MemoryOperation(scope, category, MemoryAction.CREATE, note_ref, "测试标题", content)


def test_memory_paths_keep_project_and_user_roots_separate(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    assert (
        paths.note_for(MemoryScope.PROJECT, "project-note")
        == tmp_path / "project-memory/project-note.md"
    )
    assert paths.note_for(MemoryScope.USER, "user-note") == tmp_path / "user-memory/user-note.md"


def test_memory_paths_for_workspace_use_project_local_okcode_directory(tmp_path: Path) -> None:
    paths = MemoryPaths.for_workspace(tmp_path)

    assert paths.project_root == tmp_path / ".okcode" / "memory" / "project"
    assert paths.user_root == tmp_path / ".okcode" / "memory" / "user"


def test_apply_writes_frontmatter_notes_and_two_indexes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    update = MemoryUpdate(
        operations=(
            _create(MemoryScope.USER, MemoryCategory.PREFERENCE, "python-preference"),
            _create(MemoryScope.PROJECT, MemoryCategory.PROJECT_KNOWLEDGE, "architecture"),
        ),
        user_index=(_entry("python-preference", MemoryCategory.PREFERENCE, "偏好 Python"),),
        project_index=(_entry("architecture", MemoryCategory.PROJECT_KNOWLEDGE, "项目架构"),),
    )

    store.apply(update)

    user_note = _paths(tmp_path).note_for(MemoryScope.USER, "python-preference")
    project_note = _paths(tmp_path).note_for(MemoryScope.PROJECT, "architecture")
    assert "---\nid: python-preference\nscope: user\ncategory: preference" in user_note.read_text(
        encoding="utf-8"
    )
    assert project_note.is_file()
    context = store.read_context()
    assert "用户级长期记忆" in context
    assert "项目级长期记忆" in context
    assert "偏好 Python" in context
    assert "项目架构" in context


def test_append_updates_existing_note_without_duplicate_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    create = _create(MemoryScope.USER, MemoryCategory.CORRECTION, "feedback", "初始反馈")
    initial = MemoryUpdate(
        (create,),
        (_entry("feedback", MemoryCategory.CORRECTION),),
        (),
    )
    store.apply(initial)
    append = MemoryOperation(
        MemoryScope.USER,
        MemoryCategory.CORRECTION,
        MemoryAction.APPEND,
        "feedback",
        content="补充反馈",
    )

    store.apply(MemoryUpdate((append,), initial.user_index, ()))

    notes = list(_paths(tmp_path).user_root.glob("*.md"))
    assert [path.name for path in notes if path.name != "index.md"] == ["feedback.md"]
    content = _paths(tmp_path).note_for(MemoryScope.USER, "feedback").read_text(encoding="utf-8")
    assert "初始反馈" in content
    assert "补充反馈" in content
    assert "## 更新" in content


def test_read_context_accepts_legacy_cp936_index(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.user_root.mkdir(parents=True)
    paths.index_for(MemoryScope.USER).write_bytes(
        "# 长期记忆索引\n- 用户昵称为鹏鹏\n".encode("cp936")
    )

    context = _store(tmp_path).read_context()

    assert "用户级长期记忆" in context
    assert "用户昵称为鹏鹏" in context


def test_append_rewrites_legacy_cp936_note_as_utf8(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.user_root.mkdir(parents=True)
    note = paths.note_for(MemoryScope.USER, "legacy")
    note.write_bytes(
        (
            "---\n"
            "id: legacy\n"
            "scope: user\n"
            "category: preference\n"
            "created_at: 2026-07-30T10:00:00+00:00\n"
            "updated_at: 2026-07-30T10:00:00+00:00\n"
            'title: "旧笔记"\n'
            "---\n\n"
            "用户昵称为鹏鹏。\n"
        ).encode("cp936")
    )
    append = MemoryOperation(
        MemoryScope.USER,
        MemoryCategory.PREFERENCE,
        MemoryAction.APPEND,
        "legacy",
        content="继续保留昵称。",
    )

    _store(tmp_path).apply(
        MemoryUpdate((append,), (_entry("legacy", MemoryCategory.PREFERENCE),), ())
    )

    content = note.read_bytes().decode("utf-8")
    assert "用户昵称为鹏鹏。" in content
    assert "继续保留昵称。" in content


def test_noop_does_not_create_memory_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    noop = MemoryOperation(
        MemoryScope.USER,
        MemoryCategory.PREFERENCE,
        MemoryAction.NOOP,
    )

    store.apply(MemoryUpdate((noop,), (), ()))

    assert _paths(tmp_path).user_root.exists() is False
    assert _paths(tmp_path).project_root.exists() is False


def test_apply_rejects_missing_index_reference_before_writing_notes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    update = MemoryUpdate(
        (_create(MemoryScope.USER, MemoryCategory.PREFERENCE, "created"),),
        (_entry("missing", MemoryCategory.PREFERENCE),),
        (),
    )

    with pytest.raises(ValueError, match="不存在"):
        store.apply(update)

    assert _paths(tmp_path).note_for(MemoryScope.USER, "created").exists() is False


def test_apply_rejects_invalid_note_reference(tmp_path: Path) -> None:
    store = _store(tmp_path)
    update = MemoryUpdate(
        (_create(MemoryScope.USER, MemoryCategory.PREFERENCE, "../outside"),),
        (),
        (),
    )

    with pytest.raises(ValueError, match="标识"):
        store.apply(update)


def test_apply_rejects_index_over_line_limit(tmp_path: Path) -> None:
    store = _store(tmp_path, max_index_lines=2)
    update = MemoryUpdate(
        (
            _create(MemoryScope.USER, MemoryCategory.PREFERENCE, "one"),
            _create(MemoryScope.USER, MemoryCategory.PREFERENCE, "two"),
        ),
        (
            _entry("one", MemoryCategory.PREFERENCE),
            _entry("two", MemoryCategory.PREFERENCE),
        ),
        (),
    )

    with pytest.raises(ValueError, match="行数"):
        store.apply(update)
    assert _paths(tmp_path).note_for(MemoryScope.USER, "one").exists() is False


def test_apply_rejects_index_over_byte_limit(tmp_path: Path) -> None:
    store = _store(tmp_path, max_index_bytes=30)
    update = MemoryUpdate(
        (_create(MemoryScope.USER, MemoryCategory.PREFERENCE, "one"),),
        (_entry("one", MemoryCategory.PREFERENCE, "很长的摘要"),),
        (),
    )

    with pytest.raises(ValueError, match="字节"):
        store.apply(update)
