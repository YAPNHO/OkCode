from __future__ import annotations

from okcode.agents.launcher import AgentLauncher
from okcode.agents.manager import AgentTaskManager
from okcode.agents.models import (
    AgentExecutionMode,
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
from okcode.permissions.models import PermissionMode
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
    registry.register(FakeTool("write_file"))
    return registry


def _role() -> AgentRole:
    return AgentRole(
        "reviewer",
        "审查",
        AgentRoleSourceKind.PROJECT,
        __file__,  # type: ignore[arg-type]
        tool_allowlist=("read_file",),
        permission_policy=AgentPermissionPolicy(resolved_mode=PermissionMode.STRICT),
        system_prompt="审查",
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


async def test_launcher_forces_fork_background_and_preserves_parent_messages() -> None:
    manager = RecordingManager()
    parent = _parent()
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": _role()}), _registry(), manager)  # type: ignore[arg-type]

    await launcher.launch_from_tool(AgentToolRequest(AgentLaunchKind.FORK, "继续"), parent)

    request = manager.requests[0]
    assert request.execution_mode is AgentExecutionMode.BACKGROUND
    assert request.kind is AgentLaunchKind.FORK
    assert request.parent_messages == parent.messages


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
