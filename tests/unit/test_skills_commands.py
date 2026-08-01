from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from okcode.commands import (
    CommandContext,
    CommandDefinition,
    CommandDispatcher,
    CommandKind,
    CommandRegistry,
    CommandResult,
    RuntimeMode,
    build_default_command_registry,
)
from okcode.commands.completion import SlashCommandCompleter
from okcode.models import CommandHelp, SkillListEvent
from okcode.skills.activation import SkillActivationStore
from okcode.skills.catalog import SkillCatalog
from okcode.skills.discovery import SkillRoots, discover_skills
from okcode.skills.models import SkillValidationError
from okcode.skills.runtime import SkillRuntime
from okcode.skills.tools import LoadSkillTool
from okcode.tools.models import ToolFailure
from okcode.tools.registry import ToolRegistry


class DummyConversation:
    @property
    def runtime_mode(self) -> RuntimeMode:
        return RuntimeMode.DEFAULT


def _noop(*_: object) -> CommandResult:
    return CommandResult()


def _write_skill(root: Path, name: str, description: str, body: str = "完整 SOP。") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "tools: []\n"
        "mode: shared\n"
        "history: recent\n"
        "model: null\n"
        "---\n\n" + body,
        encoding="utf-8",
    )
    return path


def _roots(tmp_path: Path) -> SkillRoots:
    return SkillRoots(tmp_path / "builtin", tmp_path / "user", tmp_path / "project")


def _runtime(roots: SkillRoots) -> SkillRuntime:
    commands = build_default_command_registry()
    runtime = SkillRuntime(SkillCatalog(roots, set()), SkillActivationStore(), commands)
    runtime.refresh()
    return runtime


@pytest.mark.asyncio
async def test_effective_skills_are_visible_as_dynamic_commands_without_exposing_sop(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    _write_skill(roots.builtin, "commit", "生成提交信息", "commit 完整 SOP。")
    _write_skill(roots.builtin, "test", "运行测试", "test 完整 SOP。")
    runtime = _runtime(roots)
    assert runtime.command_registry is not None
    registry = runtime.command_registry
    context = CommandContext(object(), registry, DummyConversation(), tmp_path, runtime)
    dispatcher = CommandDispatcher(registry)

    listed = await dispatcher.dispatch("/skill", context)
    helped = await dispatcher.dispatch("/help", context)
    forwarded = await dispatcher.dispatch("/commit 提交当前改动", context)
    forwarded_without_task = await dispatcher.dispatch("/commit", context)

    assert listed.command_result is not None
    assert isinstance(listed.command_result.events[0], SkillListEvent)
    assert [item.name for item in listed.command_result.events[0].entries] == ["commit", "test"]
    assert helped.command_result is not None
    assert isinstance(helped.command_result.events[0], CommandHelp)
    assert {item.name for item in helped.command_result.events[0].entries} >= {
        "commit",
        "review",
        "test",
    }
    assert [item.text for item in registry.completion_candidates("t")] == ["/tasks", "/test"]
    completions = list(
        SlashCommandCompleter(registry).get_completions(
            Document("/co"), CompleteEvent(completion_requested=True)
        )
    )
    assert [item.text for item in completions] == ["/commit", "/compact"]
    assert registry.resolve("commit").description == "使用 Skill（builtin）：生成提交信息"  # type: ignore[union-attr]
    assert registry.resolve("review").description == "请求代码审查。"  # type: ignore[union-attr]
    assert "commit 完整 SOP" not in runtime.render_available_section()
    assert forwarded.command_result is not None
    assert forwarded.command_result.forward is not None
    assert "'commit'" in forwarded.command_result.forward.content
    assert "提交当前改动" in forwarded.command_result.forward.content
    assert "load_skill" in forwarded.command_result.forward.content
    assert "commit 完整 SOP" not in forwarded.command_result.forward.content
    assert forwarded_without_task.command_result is not None
    assert forwarded_without_task.command_result.forward is not None
    assert "'commit'" in forwarded_without_task.command_result.forward.content
    assert "基于当前工作区" in forwarded_without_task.command_result.forward.content


def test_project_override_registers_only_the_effective_skill_command(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _write_skill(roots.builtin, "commit", "内置版本")
    _write_skill(roots.user, "commit", "用户版本")
    _write_skill(roots.project, "commit", "项目版本")

    runtime = _runtime(roots)
    assert runtime.command_registry is not None

    assert [
        (item.name, item.description, item.source.value) for item in runtime.catalog.list()
    ] == [("commit", "项目版本", "project")]
    command = runtime.command_registry.resolve("commit")
    assert command is not None
    assert command.description == "使用 Skill（project）：项目版本"
    assert [item.name for item in runtime.command_registry.definitions()].count("commit") == 1


def test_refresh_updates_catalog_and_commands_together_and_keeps_activation_snapshot(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    commit = _write_skill(roots.project, "commit", "提交", "旧 commit SOP。")
    runtime = _runtime(roots)
    assert runtime.command_registry is not None
    runtime.activation_store.activate(runtime.catalog.load_definition("commit"), {})
    test = _write_skill(roots.project, "test", "测试")

    runtime.refresh()
    assert runtime.command_registry.resolve("commit") is not None
    assert runtime.command_registry.resolve("test") is not None

    commit.unlink()
    runtime.refresh()
    assert runtime.command_registry.resolve("commit") is None
    assert runtime.command_registry.resolve("test") is not None
    assert "旧 commit SOP" in runtime.render_active_section()

    test.write_text("---\nname: test\n---\n破损", encoding="utf-8")
    runtime.refresh()
    assert [item.name for item in runtime.catalog.list()] == []
    assert runtime.command_registry.resolve("test") is None
    assert "旧 commit SOP" in runtime.render_active_section()

    review = _write_skill(roots.project, "review", "冲突 Skill")
    with pytest.raises(SkillValidationError, match=r"/review.*Skill 来源"):
        runtime.refresh()

    assert review in roots.project.iterdir()
    assert [item.name for item in runtime.catalog.list()] == []
    assert runtime.command_registry.resolve("test") is None
    assert "旧 commit SOP" in runtime.render_active_section()

    skill_result = asyncio.run(
        CommandDispatcher(runtime.command_registry).dispatch(
            "/skill",
            CommandContext(
                object(),
                runtime.command_registry,
                DummyConversation(),
                tmp_path,
                runtime,
            ),
        )
    )
    assert skill_result.command_result is not None
    event = skill_result.command_result.events[0]
    assert isinstance(event, SkillListEvent)
    assert any("/review" in issue for issue in event.issues)

    tool = LoadSkillTool(
        runtime.catalog,
        runtime.activation_store,
        ToolRegistry(),
        refresh_callback=runtime.refresh,
    )
    with pytest.raises(ToolFailure, match=r"/review"):
        asyncio.run(tool.execute({"name": "review"}))


def test_load_skill_refreshes_dynamic_commands_before_activation(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _write_skill(roots.project, "commit", "提交")
    runtime = _runtime(roots)
    assert runtime.command_registry is not None
    _write_skill(roots.project, "test", "测试")
    tool = LoadSkillTool(
        runtime.catalog,
        runtime.activation_store,
        ToolRegistry(),
        refresh_callback=runtime.refresh,
    )

    asyncio.run(tool.execute({"name": "commit"}))

    assert runtime.command_registry.resolve("test") is not None
    assert [item.name for item in runtime.catalog.list()] == ["commit", "test"]
    assert "执行旧 SOP" not in runtime.render_active_section()
    assert "完整 SOP" in runtime.render_active_section()


def test_static_review_command_rejects_external_review_skill_at_startup(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    review = _write_skill(roots.user, "review", "外部审查")

    with pytest.raises(SkillValidationError, match=r"/review.*Skill 来源") as raised:
        _runtime(roots)

    assert str(review) in str(raised.value)


def test_builtin_skill_templates_exclude_review(tmp_path: Path) -> None:
    builtin_root = SkillRoots.for_workspace(tmp_path).builtin

    result = discover_skills(SkillRoots(builtin_root, tmp_path / "user", tmp_path / "project"))

    assert [item.name for item in result.effective] == ["commit", "test"]


def test_static_command_alias_rejects_same_named_skill(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    skill = _write_skill(roots.project, "commit", "冲突 Skill")
    commands = CommandRegistry(
        (
            CommandDefinition(
                "built_in",
                ("commit",),
                "内置命令",
                "/built_in",
                CommandKind.LOCAL,
                None,
                False,
                _noop,
            ),
        )
    )
    runtime = SkillRuntime(SkillCatalog(roots, set()), SkillActivationStore(), commands)

    with pytest.raises(SkillValidationError, match=r"/commit.*?/built_in") as raised:
        runtime.refresh()

    assert str(skill) in str(raised.value)
    assert commands.resolve("commit") is not None
