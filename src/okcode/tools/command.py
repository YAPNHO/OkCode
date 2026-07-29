"""工作区内命令执行工具。"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import Mapping

from okcode.tools.files import _object_schema
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.workspace import Workspace

_STREAM_LIMIT = 6_000


class RunCommandTool:
    """在工作区中通过当前平台 shell 执行命令。"""

    def __init__(self, workspace: Workspace, *, timeout_seconds: float = 30) -> None:
        self._workspace = workspace
        self._definition = ToolDefinition(
            name="run_command",
            description="在当前工作区执行 shell 命令并返回退出状态、标准输出和标准错误。",
            input_schema=_object_schema(
                {"command": {"type": "string", "minLength": 1}}, ["command"]
            ),
            timeout_seconds=timeout_seconds,
            safety=ToolSafety.SIDE_EFFECT,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        command = str(arguments["command"])
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._workspace.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(_read_limited(process.stdout))
        stderr_task = asyncio.create_task(_read_limited(process.stderr))
        try:
            exit_code = await process.wait()
        except asyncio.CancelledError:
            await _terminate_process_tree(process)
            raise
        finally:
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task

        data: dict[str, JSONValue] = {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }
        truncated = stdout_truncated or stderr_truncated
        if exit_code != 0:
            raise ToolFailure(
                ToolErrorCode.COMMAND_FAILED,
                f"命令以退出码 {exit_code} 结束。",
                data,
            )
        return ToolOutput("命令执行成功。", data=data, truncated=truncated)


async def _read_limited(reader: asyncio.StreamReader) -> tuple[str, bool]:
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while chunk := await reader.read(4096):
        remaining = _STREAM_LIMIT - size
        if remaining > 0:
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks).decode("utf-8", errors="replace"), truncated


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    await process.wait()
