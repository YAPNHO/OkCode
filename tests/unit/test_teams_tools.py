from __future__ import annotations

from pathlib import Path

import pytest

from okcode.teams.models import TeamActorKind, TeamToolContext
from okcode.teams.runtime import TeamRuntime
from okcode.tools.defaults import build_default_registry, build_team_registry
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolErrorCode
from okcode.tools.workspace import Workspace
from tests.unit.test_teams_runtime import _runtime


def _tool_names(runtime: TeamRuntime | None, context: TeamToolContext | None, tmp_path: Path):
    registry = build_team_registry(
        build_default_registry(Workspace(tmp_path)),
        runtime=runtime,
        context=context,
    )
    return {definition.name for definition in registry.definitions()}


def test_default_registry_does_not_include_team_tools(tmp_path: Path) -> None:
    names = {
        definition.name for definition in build_default_registry(Workspace(tmp_path)).definitions()
    }

    assert "team_task" not in names
    assert "team_message" not in names


def test_team_tool_visibility_depends_on_actor_kind(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    lead = TeamToolContext("core", "lead", TeamActorKind.LEAD)
    member = TeamToolContext("core", "worker", TeamActorKind.MEMBER)

    assert _tool_names(runtime, lead, tmp_path) >= {
        "team_task",
        "team_message",
        "team_member",
        "team_merge",
    }
    assert _tool_names(runtime, member, tmp_path) >= {"team_task", "team_message"}
    assert "team_member" not in _tool_names(runtime, member, tmp_path)
    assert "team_task" not in _tool_names(runtime, None, tmp_path)


@pytest.mark.asyncio
async def test_team_task_tool_calls_runtime(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.create_team("core", "session-1")
    context = TeamToolContext("core", "lead", TeamActorKind.LEAD)
    registry = build_team_registry(
        build_default_registry(Workspace(tmp_path)),
        runtime=runtime,
        context=context,
    )
    result = await ToolExecutor(registry).execute(
        type(
            "Call",
            (),
            {
                "id": "call-1",
                "name": "team_task",
                "arguments_json": (
                    '{"action":"create","title":"build","body":"do it","dependencies":["task-0"]}'
                ),
            },
        )()
    )

    assert result.success is True
    assert result.data["task_id"].startswith("task-")
    assert result.data["dependencies"] == ["task-0"]


@pytest.mark.asyncio
async def test_team_tool_schema_rejects_missing_action(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.create_team("core", "session-1")
    registry = build_team_registry(
        build_default_registry(Workspace(tmp_path)),
        runtime=runtime,
        context=TeamToolContext("core", "lead", TeamActorKind.LEAD),
    )

    result = await ToolExecutor(registry).execute(
        type("Call", (), {"id": "call-1", "name": "team_task", "arguments_json": "{}"})()
    )

    assert result.success is False
    assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS
