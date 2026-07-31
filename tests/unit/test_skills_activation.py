from __future__ import annotations

from pathlib import Path

import pytest

from okcode.skills.activation import SkillActivationStore
from okcode.skills.models import (
    SkillDefinition,
    SkillExecutionMode,
    SkillHistoryMode,
    SkillMetadata,
    SkillSourceKind,
    SkillValidationError,
)


def _definition(name: str, *, model: str | None = None) -> SkillDefinition:
    metadata = SkillMetadata(
        name=name,
        description=f"{name} 说明",
        allowed_tools=("read_file",),
        execution_mode=SkillExecutionMode.SHARED,
        history_mode=SkillHistoryMode.RECENT,
        model=model,
        source=SkillSourceKind.PROJECT,
        source_path=Path(f"{name}.md"),
        entry_path=Path(f"{name}.md"),
        package_dir=None,
        version_id=f"{name}-v1",
        has_body=True,
    )
    return SkillDefinition(metadata, f"执行 {name}：{{{{task}}}}", ("task",))


def test_activation_snapshot_replaces_by_name_and_renders_stably() -> None:
    store = SkillActivationStore()
    first = store.activate(_definition("review"), {"task": "旧任务"})
    replacement = store.activate(_definition("review"), {"task": "新任务"})
    store.activate(_definition("test"), {"task": "测试"})

    assert first.version_id == replacement.version_id
    assert [item.name for item in store.active()] == ["review", "test"]
    rendered = store.render_active_section()
    assert "执行 review：新任务" in rendered
    assert "执行 review：旧任务" not in rendered
    assert store.visible_tool_names(("write_file",), load_skill_name="load_skill") == (
        "load_skill",
        "read_file",
    )


def test_conflicting_models_are_rejected_without_changing_active_snapshot() -> None:
    store = SkillActivationStore()
    store.activate(_definition("one", model="model-a"), {"task": "a"})

    with pytest.raises(SkillValidationError, match="模型覆盖冲突"):
        store.activate(_definition("two", model="model-b"), {"task": "b"})

    assert [item.name for item in store.active()] == ["one"]
    assert store.model_override() == "model-a"
