from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from okcode.models import AppConfig, ProviderConfig, ProviderProtocol, TeamFeatureConfig
from okcode.teams.coordinator import (
    COORDINATOR_ENV,
    CoordinatorCommandGuard,
    CoordinatorPolicy,
    GuardedRunCommandTool,
)
from okcode.teams.models import TeamActorKind, TeamToolContext
from okcode.teams.runtime import TeamRuntime
from okcode.tools.defaults import build_default_registry, build_team_registry
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.workspace import Workspace


class FakeCommandTool:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, JSONValue]] = []

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="run_command",
            description="运行命令",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            timeout_seconds=1,
            safety=ToolSafety.SIDE_EFFECT,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        self.calls.append(arguments)
        return ToolOutput("ok", {"command": arguments["command"]})


def _config(enabled: bool) -> AppConfig:
    return AppConfig(
        active="test",
        providers=(
            ProviderConfig(
                name="test",
                protocol=ProviderProtocol.OPENAI,
                model="model",
                base_url="https://example.com",
                api_key="secret",
            ),
        ),
        team=TeamFeatureConfig(coordinator_enabled=enabled),
    )


def test_coordinator_requires_config_and_environment_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = CoordinatorPolicy()

    monkeypatch.delenv(COORDINATOR_ENV, raising=False)
    assert policy.is_enabled(_config(False), {}) is False
    assert policy.is_enabled(_config(True), {}) is False
    assert policy.is_enabled(_config(False), {COORDINATOR_ENV: "1"}) is False
    assert policy.is_enabled(_config(True), {COORDINATOR_ENV: "1"}) is True


def test_coordinator_filters_write_tools_but_keeps_read_shell_and_team_tools(
    tmp_path: Path,
) -> None:
    base = build_default_registry(Workspace(tmp_path))
    runtime = TeamRuntime()
    context = TeamToolContext("core", "lead", TeamActorKind.LEAD, coordinator=True)
    registry = build_team_registry(base, runtime=runtime, context=context)

    names = set(CoordinatorPolicy().filter_tool_names(registry))

    assert {"read_file", "find_files", "search_code", "run_command"} <= names
    assert {"team_task", "team_message", "team_member", "team_merge"} <= names
    assert "write_file" not in names
    assert "edit_file" not in names


@pytest.mark.asyncio
async def test_guarded_run_command_blocks_obvious_writes_and_allows_git_status() -> None:
    inner = FakeCommandTool()
    tool = GuardedRunCommandTool(inner, CoordinatorCommandGuard())

    allowed = await tool.execute({"command": "git status --short"})
    with pytest.raises(ToolFailure) as denied:
        await tool.execute({"command": "Set-Content file.txt hi"})

    assert allowed.content == "ok"
    assert inner.calls == [{"command": "git status --short"}]
    assert denied.value.code is ToolErrorCode.PERMISSION_DENIED


def test_coordinator_instruction_is_explicit() -> None:
    instruction = CoordinatorPolicy().build_instruction()

    assert instruction.kind == "team_coordinator"
    assert "coordinator" in instruction.content
    assert "不要直接编辑业务文件" in instruction.content
