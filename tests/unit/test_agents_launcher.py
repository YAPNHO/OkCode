from __future__ import annotations

import pytest

from okcode.agents.launcher import AgentLauncher
from okcode.agents.manager import AgentTaskManager
from okcode.agents.models import (
    AgentExecutionMode,
    AgentIsolationMode,
    AgentLaunchKind,
    AgentPermissionPolicy,
    AgentRole,
    AgentRoleCatalog,
    AgentRoleSourceKind,
    AgentTaskResult,
    AgentTaskStatus,
    AgentToolRequest,
    ParentAgentContext,
)
from okcode.commands.models import RuntimeMode
from okcode.errors import ConfigError
from okcode.permissions.models import PermissionMode
from okcode.tools.models import ToolSafety
from okcode.tools.registry import ToolRegistry
from tests.unit.test_agents_filtering import FakeTool


class RecordingManager:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return AgentTaskResult(
            request.task_id, request.kind, AgentTaskStatus.COMPLETED, summary="done"
        )

    def start(self, request):
        self.requests.append(request)
        return AgentTaskManager.__new__(AgentTaskManager)._snapshot  # pragma: no cover


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FakeTool("agent"))
    registry.register(FakeTool("read_file"))
    registry.register(FakeTool("write_file", ToolSafety.SIDE_EFFECT))
    return registry


def _role(*, isolation: AgentIsolationMode = AgentIsolationMode.SHARED) -> AgentRole:
    return AgentRole(
        "reviewer",
        "审查",
        AgentRoleSourceKind.PROJECT,
        __file__,  # type: ignore[arg-type]
        tool_allowlist=("read_file",),
        permission_policy=AgentPermissionPolicy(resolved_mode=PermissionMode.STRICT),
        system_prompt="审查",
        isolation=isolation,
    )


def _parent() -> ParentAgentContext:
    return ParentAgentContext(
        "parent",
        (),
        RuntimeMode.DEFAULT,
        PermissionMode.DEFAULT,
        ("agent", "read_file", "write_file"),
    )


async def test_launcher_builds_defined_foreground_request() -> None:
    manager = RecordingManager()
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": _role()}), _registry(), manager)  # type: ignore[arg-type]

    result = await launcher.launch_from_tool(
        AgentToolRequest(AgentLaunchKind.DEFINED, "审查", role="reviewer"),
        _parent(),
    )

    request = manager.requests[0]
    assert result.status is AgentTaskStatus.COMPLETED
    assert request.execution_mode is AgentExecutionMode.FOREGROUND
    assert request.role.name == "reviewer"
    assert request.permission_mode is PermissionMode.STRICT
    assert request.visible_tool_names == ("read_file",)
    assert request.isolation is AgentIsolationMode.SHARED
    assert request.worktree_request is None


async def test_launcher_forces_fork_background_and_preserves_parent_messages() -> None:
    manager = RecordingManager()
    parent = _parent()
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": _role()}), _registry(), manager)  # type: ignore[arg-type]

    await launcher.launch_from_tool(AgentToolRequest(AgentLaunchKind.FORK, "继续"), parent)

    request = manager.requests[0]
    assert request.execution_mode is AgentExecutionMode.BACKGROUND
    assert request.kind is AgentLaunchKind.FORK
    assert request.parent_messages == parent.messages
    assert request.visible_tool_names == ("read_file",)
    assert request.isolation is AgentIsolationMode.SHARED


async def test_launcher_uses_role_worktree_isolation() -> None:
    manager = RecordingManager()
    role = _role(isolation=AgentIsolationMode.WORKTREE)
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": role}), _registry(), manager)  # type: ignore[arg-type]

    await launcher.launch_from_tool(
        AgentToolRequest(AgentLaunchKind.DEFINED, "审查", role="reviewer"),
        _parent(),
    )

    request = manager.requests[0]
    assert request.isolation is AgentIsolationMode.WORKTREE
    assert request.worktree_request is not None
    assert request.worktree_request.identity.role_name == "reviewer"
    assert request.worktree_request.identity.name.startswith("agents/reviewer/")
    assert request.worktree_request.identity.branch.startswith("okcode/agents/")
    assert request.worktree_request.main_workspace == _parent().workspace_root
    assert request.main_workspace_root == _parent().workspace_root


async def test_launcher_request_can_upgrade_fork_to_worktree() -> None:
    manager = RecordingManager()
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": _role()}), _registry(), manager)  # type: ignore[arg-type]

    await launcher.launch_from_tool(
        AgentToolRequest(
            AgentLaunchKind.FORK,
            "继续",
            isolation=AgentIsolationMode.WORKTREE,
            worktree_name="agents/fork/custom",
        ),
        _parent(),
    )

    request = manager.requests[0]
    assert request.execution_mode is AgentExecutionMode.BACKGROUND
    assert request.isolation is AgentIsolationMode.WORKTREE
    assert request.visible_tool_names == ("read_file", "write_file")
    assert request.worktree_request is not None
    assert request.worktree_request.identity.name == "agents/fork/custom"
    assert request.worktree_request.identity.role_name is None


async def test_launcher_request_shared_does_not_downgrade_role_worktree() -> None:
    manager = RecordingManager()
    role = _role(isolation=AgentIsolationMode.WORKTREE)
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": role}), _registry(), manager)  # type: ignore[arg-type]

    await launcher.launch_from_tool(
        AgentToolRequest(
            AgentLaunchKind.DEFINED,
            "审查",
            role="reviewer",
            isolation=AgentIsolationMode.SHARED,
        ),
        _parent(),
    )

    request = manager.requests[0]
    assert request.isolation is AgentIsolationMode.WORKTREE
    assert request.worktree_request is not None


async def test_launcher_rejects_invalid_worktree_name() -> None:
    manager = RecordingManager()
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": _role()}), _registry(), manager)  # type: ignore[arg-type]

    with pytest.raises(ConfigError, match="worktree 名称"):
        await launcher.launch_from_tool(
            AgentToolRequest(
                AgentLaunchKind.FORK,
                "继续",
                isolation=AgentIsolationMode.WORKTREE,
                worktree_name="../bad",
            ),
            _parent(),
        )


async def test_launcher_rejects_unknown_role() -> None:
    manager = RecordingManager()
    launcher = AgentLauncher(AgentRoleCatalog({}), _registry(), manager)  # type: ignore[arg-type]

    try:
        await launcher.launch_from_tool(
            AgentToolRequest(AgentLaunchKind.DEFINED, "审查", role="missing"),
            _parent(),
        )
    except LookupError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("未知角色应失败")
