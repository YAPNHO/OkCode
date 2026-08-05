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
    MemoryScopeUsage,
    MemoryUpdate,
)
from okcode.memory.store import MemoryStore


def _now() -> datetime:
    return datetime(2026, 7, 30, 10, 0, tzinfo=UTC)


def _paths(tmp_path: Path) -> MemoryPaths:
    return MemoryPaths(tmp_path / "project-memory", tmp_path / "user-memory")


def _store(tmp_path: Path, **kwargs: object) -> MemoryStore:
    return MemoryStore(_paths(tmp_path), clock=_now, **kwargs)


def _entry(name: str, category: MemoryCategory, summary: str = "摘要") -> MemoryIndexEntry:
    return MemoryIndexEntry(name, category, summary)


def _create(
    scope: MemoryScope,
    category: MemoryCategory,
    name: str,
    content: str = "笔记正文",
) -> MemoryOperation:
    return MemoryOperation(scope, category, MemoryAction.CREATE, name, "测试摘要", content)


def test_memory_paths_keep_project_and_user_roots_separate(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    assert (
        paths.note_for(MemoryScope.PROJECT, "project-note")
        == tmp_path / "project-memory/project-note.md"
    )
    assert paths.note_for(MemoryScope.USER, "user-note") == tmp_path / "user-memory/user-note.md"
    assert paths.index_for(MemoryScope.USER) == tmp_path / "user-memory/MEMORY.md"
    assert paths.legacy_index_for(MemoryScope.USER) == tmp_path / "user-memory/index.md"


def test_memory_paths_allow_unicode_and_spaces_but_reject_unsafe_names(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    assert paths.note_for(MemoryScope.USER, "回答 风格") == tmp_path / "user-memory/回答 风格.md"
    for name in ("../outside", "bad/name", "bad|name", "CON", "MEMORY", "trailing "):
        with pytest.raises(ValueError):
            paths.note_for(MemoryScope.USER, name)


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
    user_content = user_note.read_text(encoding="utf-8")
    assert 'name: "python-preference"' in user_content
    assert 'summary: "测试摘要"' in user_content
    assert project_note.is_file()
    index = _paths(tmp_path).index_for(MemoryScope.USER).read_text(encoding="utf-8")
    assert index == "[python-preference.md](python-preference.md) | 偏好 Python\n"
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
    assert [path.name for path in notes if path.name != "MEMORY.md"] == ["feedback.md"]
    content = _paths(tmp_path).note_for(MemoryScope.USER, "feedback").read_text(encoding="utf-8")
    assert "初始反馈" in content
    assert "补充反馈" in content
    assert "## 更新" in content


def test_append_preserves_metadata_and_can_update_summary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = MemoryUpdate(
        (_create(MemoryScope.USER, MemoryCategory.CORRECTION, "feedback"),),
        (_entry("feedback", MemoryCategory.CORRECTION, "初始摘要"),),
        (),
    )
    store.apply(initial)

    store.apply(
        MemoryUpdate(
            (
                MemoryOperation(
                    MemoryScope.USER,
                    MemoryCategory.CORRECTION,
                    MemoryAction.APPEND,
                    "feedback",
                    "更新后的摘要",
                    "补充内容",
                ),
            ),
            (_entry("feedback", MemoryCategory.CORRECTION, "更新后的摘要"),),
            (),
        )
    )

    content = _paths(tmp_path).note_for(MemoryScope.USER, "feedback").read_text(encoding="utf-8")
    assert 'name: "feedback"' in content
    assert "category: correction" in content
    assert 'created_at: "2026-07-30T10:00:00+00:00"' in content
    assert 'summary: "更新后的摘要"' in content
    assert "补充内容" in content


def test_read_context_accepts_legacy_cp936_index(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.user_root.mkdir(parents=True)
    paths.legacy_index_for(MemoryScope.USER).write_bytes(
        "# 长期记忆索引\n- 用户昵称为鹏鹏\n".encode("cp936")
    )

    context = _store(tmp_path).read_context()

    assert "用户级长期记忆" in context
    assert "用户昵称为鹏鹏" in context


def test_read_context_prefers_new_index_over_legacy_index(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.user_root.mkdir(parents=True)
    paths.index_for(MemoryScope.USER).write_text("新索引\n", encoding="utf-8")
    paths.legacy_index_for(MemoryScope.USER).write_text("旧索引\n", encoding="utf-8")

    context = _store(tmp_path).read_context()

    assert "新索引" in context
    assert "旧索引" not in context


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

    with pytest.raises(ValueError, match="记忆名称"):
        store.apply(update)


def test_apply_rejects_index_over_line_limit(tmp_path: Path) -> None:
    store = _store(tmp_path, max_index_lines=1)
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


def test_snapshot_counts_current_scope_markdown_files_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.project_root.mkdir(parents=True)
    paths.user_root.mkdir(parents=True)
    paths.project_root.joinpath("MEMORY.md").write_bytes(b"a" * 1025)
    paths.project_root.joinpath("note.md").write_bytes(b"b" * 7)
    paths.project_root.joinpath("ignored.txt").write_bytes(b"c" * 99)
    paths.project_root.joinpath("nested").mkdir()
    paths.project_root.joinpath("nested/nested.md").write_bytes(b"d" * 101)
    paths.user_root.joinpath("user.md").write_bytes(b"e" * 3)

    snapshot = _store(tmp_path).snapshot()

    assert snapshot.project == MemoryScopeUsage(("MEMORY.md", "note.md"), 1032)
    assert snapshot.user == MemoryScopeUsage(("user.md",), 3)
