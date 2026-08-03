from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from okcode.hooks.models import HookContext, HookEvent, HookInterception
from okcode.models import ToolCall
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import PermissionConfirmation, PermissionMode
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


class RecordingHooks:
    def __init__(self, interception: HookInterception | None = None) -> None:
        self.interception = interception
        self.contexts: list[HookContext] = []

    async def dispatch(self, context: HookContext) -> HookInterception | None:
        self.contexts.append(context)
        if context.event is HookEvent.TOOL_BEFORE:
            return self.interception
        return None


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


@pytest.mark.asyncio
async def test_executor_waits_for_async_permission_confirmation(tmp_path) -> None:
    tool = CommandTool()
    registry = ToolRegistry()
    registry.register(tool)
    paths = PermissionPaths(
        user=tmp_path / "user.yaml",
        project=tmp_path / ".okcode" / "permissions.yaml",
        project_local=tmp_path / ".okcode" / "permissions.local.yaml",
    )
    requested = asyncio.Event()
    release = asyncio.Event()

    async def confirm(_: object) -> PermissionConfirmation:
        requested.set()
        await release.wait()
        return PermissionConfirmation.ONCE

    permissions = PermissionManager(
        Workspace(tmp_path),
        (),
        paths,
        {"run_command"},
        confirmer=confirm,
    )
    task = asyncio.create_task(
        ToolExecutor(registry, permissions=permissions).execute(
            ToolCall("call", "run_command", '{"command":"git status"}')
        )
    )

    await requested.wait()
    assert task.done() is False
    release.set()

    result = await task
    assert result.success is True
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_executor_reuses_session_grant_for_different_commands(tmp_path) -> None:
    tool = CommandTool()
    registry = ToolRegistry()
    registry.register(tool)
    confirmation_count = 0

    async def confirm(_: object) -> PermissionConfirmation:
        nonlocal confirmation_count
        confirmation_count += 1
        return PermissionConfirmation.SESSION

    permissions = PermissionManager(
        Workspace(tmp_path),
        (),
        PermissionPaths.for_workspace(tmp_path),
        {"run_command"},
        confirmer=confirm,
    )
    executor = ToolExecutor(registry, permissions=permissions)

    first = await executor.execute(ToolCall("first", "run_command", '{"command":"git status"}'))
    second = await executor.execute(ToolCall("second", "run_command", '{"command":"git diff"}'))

    assert first.success is True
    assert second.success is True
    assert tool.calls == 2
    assert confirmation_count == 1


@pytest.mark.asyncio
async def test_hook_before_rejection_happens_before_tool_execution() -> None:
    tool = CommandTool()
    registry = ToolRegistry()
    registry.register(tool)
    hooks = RecordingHooks(HookInterception("Hook 拒绝写入。", "guard"))
    executor = ToolExecutor(registry, hooks=hooks)  # type: ignore[arg-type]

    result = await executor.execute(ToolCall("call", "run_command", '{"command":"echo hi"}'))

    assert result.error_code is ToolErrorCode.PERMISSION_DENIED
    assert result.data["hook_rule"] == "guard"
    assert result.data["hook_event"] == "tool.before"
    assert result.data["executed"] is False
    assert tool.calls == 0
    assert hooks.contexts[0].values["tool.arguments.command"] == "echo hi"


@pytest.mark.asyncio
async def test_hook_after_runs_after_result_is_created() -> None:
    tool = CommandTool()
    registry = ToolRegistry()
    registry.register(tool)
    hooks = RecordingHooks()
    executor = ToolExecutor(registry, hooks=hooks)  # type: ignore[arg-type]

    result = await executor.execute(ToolCall("call", "run_command", '{"command":"echo hi"}'))

    assert result.success is True
    assert tool.calls == 1
    assert [context.event for context in hooks.contexts] == [
        HookEvent.TOOL_BEFORE,
        HookEvent.TOOL_AFTER,
    ]
    assert hooks.contexts[1].values["tool.result.success"] is True
