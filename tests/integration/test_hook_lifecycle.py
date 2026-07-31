from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from okcode.hooks import HookPaths, HookRuntime, load_hook_rules
from okcode.hooks.actions import HookActionRunner, ShellCommandResult
from okcode.hooks.models import HookContext, HookEvent
from okcode.models import ToolCall
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import (
    JSONValue,
    PermissionTarget,
    PermissionTargetKind,
    ToolDefinition,
    ToolErrorCode,
    ToolOutput,
)
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace


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


async def _deny_shell(
    _command: str,
    _cwd: Path,
    stdin_text: str,
    _timeout_seconds: float,
) -> ShellCommandResult:
    return ShellCommandResult(1, stdout=stdin_text)


@pytest.mark.asyncio
async def test_hooks_yaml_drives_prompt_injection_tool_interception_and_listing(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".okcode"
    config_dir.mkdir()
    config_path = config_dir / "hooks.yaml"
    config_path.write_text(
        """
hooks:
  - name: inject-deploy-note
    event: message.user
    if:
      - field: message.content
        match: glob:*deploy*
    action:
      type: prompt
      content: 部署前先确认环境和回滚方案。
      scope: next_request
  - name: block-danger-delete
    event: tool.before
    if:
      all:
        - field: tool.name
          match: exact:run_command
        - field: tool.arguments.command
          match: regex:^rm\\s+-rf
    action:
      type: shell
      command: echo deny
      intercept: true
      deny_message: 禁止危险删除。
""".lstrip(),
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)
    paths = HookPaths.for_workspace(tmp_path)
    runtime = HookRuntime(
        load_hook_rules(paths),
        runner=HookActionRunner(workspace, shell_runner=_deny_shell),
        config_path=str(paths.config),
    )

    await runtime.dispatch(
        HookContext(HookEvent.MESSAGE_USER, {"message.content": "please deploy service"})
    )
    assert [item.content for item in runtime.system_instructions()] == [
        "[inject-deploy-note] 部署前先确认环境和回滚方案。"
    ]

    tool = CommandTool()
    registry = ToolRegistry()
    registry.register(tool)
    result = await ToolExecutor(registry, hooks=runtime).execute(
        ToolCall("call", "run_command", '{"command":"rm -rf build"}')
    )

    assert result.success is False
    assert result.error_code is ToolErrorCode.PERMISSION_DENIED
    assert result.content.startswith("禁止危险删除。")
    assert "调用未执行" in result.content
    assert result.data["hook_rule"] == "block-danger-delete"
    assert result.data["executed"] is False
    assert tool.calls == 0

    entries = runtime.list_entries()
    assert [entry.identifier for entry in entries] == [
        "inject-deploy-note",
        "block-danger-delete",
    ]
    assert entries[0].condition == "message.content=glob:*deploy*"
    assert entries[1].event == "tool.before"
