from __future__ import annotations

from pathlib import Path

import pytest

from okcode.agents.models import (
    AgentIsolationMode,
    AgentModelKind,
    AgentPermissionKind,
    AgentRoleSourceKind,
)
from okcode.agents.roles import AgentRolePaths, load_agent_roles, parse_agent_role_markdown
from okcode.errors import ConfigError
from okcode.permissions.models import PermissionMode


def _write_role(
    root: Path, name: str, *, description: str = "说明", body: str = "系统提示"
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(
        f"""---
name: {name}
description: {description}
tools:
  allow: [read_file, search_code]
  deny: [run_command]
model: inherit
max_turns: 5
permission: strict
---

{body}
""",
        encoding="utf-8",
    )
    return path


def test_parse_agent_role_markdown_reads_frontmatter_and_body(tmp_path: Path) -> None:
    path = _write_role(tmp_path, "reviewer")

    role = parse_agent_role_markdown(path, AgentRoleSourceKind.PROJECT)

    assert role.name == "reviewer"
    assert role.description == "说明"
    assert role.tool_allowlist == ("read_file", "search_code")
    assert role.tool_denylist == ("run_command",)
    assert role.model_policy.kind is AgentModelKind.INHERIT
    assert role.max_turns == 5
    assert role.permission_policy.kind is AgentPermissionKind.STRICT
    assert role.permission_policy.resolved_mode is PermissionMode.STRICT
    assert role.system_prompt == "系统提示"
    assert role.isolation is AgentIsolationMode.SHARED


def test_parse_agent_role_markdown_reads_worktree_isolation(tmp_path: Path) -> None:
    path = tmp_path / "worker.md"
    path.write_text(
        """---
name: worker
description: 隔离执行
isolation: worktree
---
系统提示
""",
        encoding="utf-8",
    )

    role = parse_agent_role_markdown(path, AgentRoleSourceKind.PROJECT)

    assert role.isolation is AgentIsolationMode.WORKTREE


@pytest.mark.parametrize(
    "content, message",
    [
        ("name: bad", "frontmatter"),
        ("---\nname: bad\n---\n正文", "description"),
        (
            "---\nname: bad\ndescription: d\nunknown: x\n---\n正文",
            "未知字段",
        ),
        (
            "---\nname: bad\ndescription: d\ntools: read_file\n---\n正文",
            "tools",
        ),
        (
            "---\nname: bad\ndescription: d\nmodel: gpt\n---\n正文",
            "model",
        ),
        (
            "---\nname: bad\ndescription: d\npermission: root\n---\n正文",
            "permission",
        ),
        (
            "---\nname: bad\ndescription: d\nisolation: process\n---\n正文",
            "isolation",
        ),
        (
            "---\nname: bad\ndescription: d\nmax_turns: 0\n---\n正文",
            "max_turns",
        ),
        (
            "---\nname: bad\ndescription: d\n---\n",
            "正文",
        ),
    ],
)
def test_invalid_role_reports_diagnostic(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "bad.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        parse_agent_role_markdown(path, AgentRoleSourceKind.PROJECT)


def test_allow_and_deny_conflict_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text(
        """---
name: bad
description: d
tools:
  allow: [read_file]
  deny: [read_file]
---
正文
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="冲突"):
        parse_agent_role_markdown(path, AgentRoleSourceKind.PROJECT)


def test_load_agent_roles_applies_source_override_priority(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    _write_role(plugin, "same", description="插件")
    _write_role(builtin, "same", description="内置")
    _write_role(user, "same", description="用户")
    _write_role(project, "same", description="项目")

    catalog = load_agent_roles(
        AgentRolePaths(plugin=(plugin,), builtin=builtin, user=user, project=project)
    )

    role = catalog.get("same")
    assert role.description == "项目"
    assert role.source_kind is AgentRoleSourceKind.PROJECT
    assert len(catalog.shadowed) == 3
    entry = catalog.list_entries()[0]
    assert entry.shadowed_count == 3


def test_builtin_roles_are_loadable() -> None:
    paths = AgentRolePaths.for_workspace(Path("missing-workspace"))

    catalog = load_agent_roles(paths)

    assert "code-reviewer" in catalog.roles
    assert "general-purpose" in catalog.roles
    assert "researcher" in catalog.roles
    general = catalog.get("general-purpose")
    assert "write_file" in general.tool_allowlist
    assert "run_command" in general.tool_allowlist
    assert general.permission_policy.kind is AgentPermissionKind.INHERIT
