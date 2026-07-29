from __future__ import annotations

import pytest

from okcode.models import ToolCall
from okcode.permissions.blacklist import reject_blacklisted_command
from okcode.permissions.models import PermissionRequest, RuleSource
from okcode.tools.models import PermissionTarget, PermissionTargetKind, ToolDefinition


def _request(command: str) -> PermissionRequest:
    call = ToolCall("call", "run_command", "{}")
    tool = ToolDefinition(
        name="run_command",
        description="测试命令工具",
        input_schema={},
        timeout_seconds=1,
        permission_target=PermissionTarget(PermissionTargetKind.COMMAND, "command"),
    )
    return PermissionRequest(
        call=call,
        tool=tool,
        arguments={"command": command},
        target_kind=PermissionTargetKind.COMMAND,
        target=command,
        display_target=command,
    )


@pytest.mark.parametrize(
    "command",
    [
        "rmdir /s /q C:\\",
        "Remove-Item -Recurse $env:SystemDrive\\*",
        "format C:",
        "diskpart /s wipe.txt",
        "Clear-Disk -Number 0 -RemoveData",
        "bcdedit /delete {current}",
        "shutdown /r /t 0",
    ],
)
def test_blacklist_rejects_high_risk_windows_commands(command: str) -> None:
    decision = reject_blacklisted_command(_request(command))

    assert decision is not None
    assert decision.allowed is False
    assert decision.source is RuleSource.BLACKLIST
    assert "正则" not in decision.reason


@pytest.mark.parametrize(
    "command",
    ["git status", "python -c \"print('ok')\"", "Get-ChildItem src"],
)
def test_blacklist_allows_normal_commands_to_continue(command: str) -> None:
    assert reject_blacklisted_command(_request(command)) is None
