from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from okcode.models import ToolCall
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import PermissionMode
from okcode.permissions.rules import PermissionPaths
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import (
    JSONValue,
    PermissionTarget,
    PermissionTargetKind,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
)
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace


class ControlledTool:
    def __init__(self, behavior: str = "success", *, timeout_seconds: float = 1) -> None:
        self._behavior = behavior
        self._definition = ToolDefinition(
            name="controlled",
            description="受控工具",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            timeout_seconds=timeout_seconds,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        if self._behavior == "failure":
            raise ToolFailure(ToolErrorCode.IO_ERROR, "预期失败", {"detail": "x"})
        if self._behavior == "exception":
            raise RuntimeError("不应泄漏")
        if self._behavior == "sleep":
            await asyncio.sleep(1)
        if self._behavior == "large":
            return ToolOutput("x" * 100, {"payload": "y" * 100})
        return ToolOutput("成功", {"value": arguments["value"]})


class CommandTool:
    def __init__(self) -> None:
        self.calls = 0
        self._definition = ToolDefinition(
            name="run_command",
            description="受控命令工具",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            timeout_seconds=1,
            permission_target=PermissionTarget(PermissionTargetKind.COMMAND, "command"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        self.calls += 1
        return ToolOutput("命令成功", {"command": arguments["command"]})


def _executor(tool: ControlledTool, **limits: int) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, **limits)


@pytest.mark.asyncio
async def test_executor_returns_success_and_tool_failure() -> None:
    call = ToolCall("id", "controlled", '{"value":"ok"}')
    success = await _executor(ControlledTool()).execute(call)
    failure = await _executor(ControlledTool("failure")).execute(call)

    assert success.success is True
    assert success.data == {"value": "ok"}
    assert failure.success is False
    assert failure.error_code is ToolErrorCode.IO_ERROR


@pytest.mark.asyncio
async def test_executor_handles_unknown_json_and_schema_errors() -> None:
    executor = _executor(ControlledTool())
    unknown = await executor.execute(ToolCall("id", "missing", "{}"))
    invalid_json = await executor.execute(ToolCall("id", "controlled", "{"))
    schema = await executor.execute(ToolCall("id", "controlled", '{"extra":1}'))

    assert unknown.error_code is ToolErrorCode.UNKNOWN_TOOL
    assert invalid_json.error_code is ToolErrorCode.INVALID_JSON
    assert schema.error_code is ToolErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_executor_handles_timeout_internal_error_and_truncation() -> None:
    call = ToolCall("id", "controlled", '{"value":"ok"}')
    timed_out = await _executor(ControlledTool("sleep", timeout_seconds=0.01)).execute(call)
    internal = await _executor(ControlledTool("exception")).execute(call)
    large = await _executor(ControlledTool("large"), content_limit=10, data_limit=20).execute(call)

    assert timed_out.error_code is ToolErrorCode.TIMEOUT
    assert internal.error_code is ToolErrorCode.INTERNAL_ERROR
    assert large.success is True
    assert large.truncated is True
    assert "输出已截断" in large.content
    assert large.data["data_truncated"] is True


@pytest.mark.asyncio
async def test_permission_rejection_happens_before_command_tool_execution(tmp_path) -> None:
    tool = CommandTool()
    registry = ToolRegistry()
    registry.register(tool)
    paths = PermissionPaths(
        user=tmp_path / "user.yaml",
        project=tmp_path / ".okcode" / "permissions.yaml",
        project_local=tmp_path / ".okcode" / "permissions.local.yaml",
    )
    permissions = PermissionManager(
        Workspace(tmp_path),
        (),
        paths,
        {"run_command"},
        mode=PermissionMode.ALLOW,
    )
    executor = ToolExecutor(registry, permissions=permissions)

    result = await executor.execute(
        ToolCall("call", "run_command", '{"command":"shutdown /r /t 0"}')
    )

    assert result.error_code is ToolErrorCode.PERMISSION_DENIED
    assert result.data["permission_source"] == "blacklist"
    assert result.data["executed"] is False
    assert tool.calls == 0
