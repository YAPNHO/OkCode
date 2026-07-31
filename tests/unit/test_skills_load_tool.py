from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from okcode.models import ChatMessage, Role, StreamCompleted
from okcode.skills.activation import SkillActivationStore
from okcode.skills.catalog import SkillCatalog
from okcode.skills.discovery import SkillRoots
from okcode.skills.runner import SkillRunner
from okcode.skills.tools import LoadSkillTool
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolErrorCode, ToolFailure
from okcode.tools.registry import ToolRegistry
from tests.fakes import FakeProvider


def _write_skill(root: Path, *, mode: str = "shared", body: str = "执行旧 SOP。") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "review.md"
    path.write_text(
        "---\n"
        "name: review\n"
        "description: 审查改动\n"
        "tools: []\n"
        f"mode: {mode}\n"
        "history: none\n"
        "model: null\n"
        "---\n\n" + body,
        encoding="utf-8",
    )
    return path


def _catalog(tmp_path: Path, *, mode: str = "shared") -> tuple[SkillCatalog, Path]:
    roots = SkillRoots(tmp_path / "builtin", tmp_path / "user", tmp_path / "project")
    path = _write_skill(roots.project, mode=mode)
    return SkillCatalog.discover(roots, set()), path


def test_load_shared_skill_uses_snapshot_until_explicit_reload(tmp_path: Path) -> None:
    catalog, path = _catalog(tmp_path)
    registry = ToolRegistry()
    store = SkillActivationStore()
    tool = LoadSkillTool(catalog, store, registry)
    registry.register(tool)

    output = asyncio.run(tool.execute({"name": "review"}))
    assert output.data["mode"] == "shared"
    assert "旧 SOP" in store.render_active_section()

    path.write_text(path.read_text(encoding="utf-8").replace("旧 SOP", "新 SOP"), encoding="utf-8")
    assert "旧 SOP" in store.render_active_section()
    asyncio.run(tool.execute({"name": "review"}))
    assert "新 SOP" in store.render_active_section()
    assert "旧 SOP" not in store.render_active_section()


def test_load_error_preserves_previous_activation(tmp_path: Path) -> None:
    catalog, path = _catalog(tmp_path)
    registry = ToolRegistry()
    store = SkillActivationStore()
    tool = LoadSkillTool(catalog, store, registry)
    registry.register(tool)
    asyncio.run(tool.execute({"name": "review"}))
    path.write_text("---\nname: review\n---\n破损", encoding="utf-8")

    with pytest.raises(ToolFailure) as raised:
        asyncio.run(tool.execute({"name": "review"}))

    assert raised.value.code is ToolErrorCode.INTERNAL_ERROR
    assert "description" in raised.value.content
    assert "旧 SOP" in store.render_active_section()


def test_isolated_skill_returns_summary_without_writing_temporary_history(tmp_path: Path) -> None:
    catalog, _ = _catalog(tmp_path, mode="isolated")
    registry = ToolRegistry()
    store = SkillActivationStore()
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "独立摘要"))])
    runner = SkillRunner(provider, executor=ToolExecutor(registry))
    tool = LoadSkillTool(
        catalog,
        store,
        registry,
        runner=runner,
        history_provider=lambda: (ChatMessage(Role.USER, "主会话问题"),),
    )
    registry.register(tool)

    output = asyncio.run(tool.execute({"name": "review"}))

    assert output.data["summary"] == "独立摘要"
    assert [message.content for message in provider.requests[0]] == [
        "请按已激活 Skill 执行任务，并返回摘要。"
    ]
