from __future__ import annotations

import json

import pytest

from okcode.agents.models import (
    AgentIsolationMode,
    AgentLaunchKind,
    AgentTaskResult,
    AgentTaskSnapshot,
    AgentTaskStatus,
    ParentAgentContext,
)
from okcode.agents.tool import AGENT_TOOL_NAME, AgentTool
from okcode.commands.models import RuntimeMode
from okcode.permissions.models import PermissionMode
from okcode.tools.models import ToolFailure


class Launcher:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.requests = []

    async def launch_from_tool(self, request, parent):
        self.requests.append((request, parent))
        return self.outcome


def _parent() -> ParentAgentContext:
    return ParentAgentContext("parent", (), RuntimeMode.DEFAULT, PermissionMode.DEFAULT, ())


async def test_agent_tool_definition_is_stable_and_defined_request_runs() -> None:
    launcher = Launcher(
        AgentTaskResult(
            "task-1", AgentLaunchKind.DEFINED, AgentTaskStatus.COMPLETED, summary="done"
        )
    )
    tool = AgentTool(launcher, _parent)

    output = await tool.execute({"kind": "defined", "task": "审查", "role": "reviewer"})

    assert tool.definition.name == AGENT_TOOL_NAME
    assert tool.definition.timeout_seconds == 600
    assert tool.definition.input_schema["required"] == ["kind", "task"]
    assert launcher.requests[0][0].role == "reviewer"
    data = json.loads(output.content)
    assert data["status"] == "completed"


async def test_agent_tool_parses_worktree_fields_for_defined_request() -> None:
    launcher = Launcher(
        AgentTaskResult(
            "task-1", AgentLaunchKind.DEFINED, AgentTaskStatus.COMPLETED, summary="done"
        )
    )
    tool = AgentTool(launcher, _parent)

    await tool.execute(
        {
            "kind": "defined",
            "task": "审查",
            "role": "reviewer",
            "isolation": "worktree",
            "worktree_name": "agents/reviewer/custom",
        }
    )

    request = launcher.requests[0][0]
    assert request.isolation is AgentIsolationMode.WORKTREE
    assert request.worktree_name == "agents/reviewer/custom"


async def test_agent_tool_parses_worktree_fields_for_fork_request() -> None:
    launcher = Launcher(
        AgentTaskSnapshot("task-1", AgentLaunchKind.FORK, AgentTaskStatus.BACKGROUND)
    )
    tool = AgentTool(launcher, _parent)

    await tool.execute(
        {
            "kind": "fork",
            "task": "继续",
            "isolation": "worktree",
            "worktree_name": "agents/fork/custom",
        }
    )

    request = launcher.requests[0][0]
    assert request.kind is AgentLaunchKind.FORK
    assert request.background is True
    assert request.isolation is AgentIsolationMode.WORKTREE
    assert request.worktree_name == "agents/fork/custom"


async def test_agent_tool_formats_background_snapshot() -> None:
    launcher = Launcher(
        AgentTaskSnapshot("task-1", AgentLaunchKind.FORK, AgentTaskStatus.BACKGROUND)
    )
    tool = AgentTool(launcher, _parent)

    output = await tool.execute({"kind": "fork", "task": "继续"})

    data = json.loads(output.content)
    assert data["status"] == "background"
    assert "后台" in data["summary"]


async def test_agent_tool_rejects_invalid_arguments() -> None:
    tool = AgentTool(Launcher(None), _parent)

    with pytest.raises(ToolFailure, match="role"):
        await tool.execute({"kind": "defined", "task": "审查"})

    with pytest.raises(ToolFailure, match="isolation"):
        await tool.execute({"kind": "fork", "task": "继续", "isolation": "process"})
