from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from okcode.models import ToolCall
from okcode.tools.command import RunCommandTool
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolErrorCode
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace


def _python_command(code: str) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", code])


async def _execute(tool: RunCommandTool, command: str):
    registry = ToolRegistry()
    registry.register(tool)
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    call = ToolCall("call", "run_command", f'{{"command":"{escaped}"}}')
    return await ToolExecutor(registry).execute(call)


@pytest.mark.asyncio
async def test_run_command_uses_workspace_and_captures_output(tmp_path: Path) -> None:
    tool = RunCommandTool(Workspace(tmp_path))
    result = await _execute(tool, _python_command("import os; print(os.getcwd())"))

    assert result.success is True
    assert str(tmp_path) in result.data["stdout"]
    assert result.data["exit_code"] == 0


@pytest.mark.asyncio
async def test_run_command_returns_nonzero_output(tmp_path: Path) -> None:
    tool = RunCommandTool(Workspace(tmp_path))
    command = _python_command("import sys; print('bad', file=sys.stderr); raise SystemExit(3)")
    result = await _execute(tool, command)

    assert result.success is False
    assert result.error_code is ToolErrorCode.COMMAND_FAILED
    assert result.data["exit_code"] == 3
    assert "bad" in result.data["stderr"]


@pytest.mark.asyncio
async def test_run_command_times_out_and_truncates_output(tmp_path: Path) -> None:
    timeout_tool = RunCommandTool(Workspace(tmp_path), timeout_seconds=0.05)
    timed_out = await _execute(timeout_tool, _python_command("import time; time.sleep(2)"))
    assert timed_out.error_code is ToolErrorCode.TIMEOUT

    output_tool = RunCommandTool(Workspace(tmp_path))
    large = await _execute(output_tool, _python_command("print('x' * 7000)"))
    assert large.success is True
    assert large.truncated is True
