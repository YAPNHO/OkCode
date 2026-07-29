from __future__ import annotations

from pathlib import Path

import pytest

from okcode.errors import ExitRequested
from okcode.models import ToolCall
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import (
    PermissionConfirmation,
    PermissionMode,
    PermissionRule,
    RuleAction,
    RuleSet,
    RuleSource,
)
from okcode.permissions.rules import PermissionPaths
from okcode.tools.models import PermissionTarget, PermissionTargetKind, ToolDefinition
from okcode.tools.workspace import Workspace


def _paths(root: Path) -> PermissionPaths:
    return PermissionPaths(
        user=root / "user.yaml",
        project=root / ".okcode" / "permissions.yaml",
        project_local=root / ".okcode" / "permissions.local.yaml",
    )


def _tool(name: str, kind: PermissionTargetKind) -> ToolDefinition:
    argument = "command" if kind is PermissionTargetKind.COMMAND else "path"
    return ToolDefinition(
        name=name,
        description="测试工具",
        input_schema={},
        timeout_seconds=1,
        permission_target=PermissionTarget(kind, argument),
    )


def _manager(
    root: Path,
    rule_sets: tuple[RuleSet, ...] = (),
    *,
    mode: PermissionMode = PermissionMode.DEFAULT,
    confirmation: PermissionConfirmation = PermissionConfirmation.DENY,
) -> PermissionManager:
    return PermissionManager(
        Workspace(root),
        rule_sets,
        _paths(root),
        {"run_command", "write_file"},
        mode=mode,
        confirmer=lambda _: confirmation,
    )


def _authorize_command(manager: PermissionManager, command: str):
    return manager.authorize(
        ToolCall("call", "run_command", "{}"),
        _tool("run_command", PermissionTargetKind.COMMAND),
        {"command": command},
    )


def test_blacklist_and_deny_override_mode_and_rules(tmp_path: Path) -> None:
    rules = (
        RuleSet(
            RuleSource.PROJECT_LOCAL,
            (
                PermissionRule("run_command", "shutdown /r /t 0", RuleAction.ALLOW),
                PermissionRule("run_command", "git status", RuleAction.DENY),
            ),
        ),
    )
    manager = _manager(
        tmp_path, rules, mode=PermissionMode.ALLOW, confirmation=PermissionConfirmation.ONCE
    )

    blacklisted = _authorize_command(manager, "shutdown /r /t 0")
    denied = _authorize_command(manager, "git status")

    assert blacklisted.allowed is False
    assert blacklisted.source is RuleSource.BLACKLIST
    assert denied.allowed is False
    assert denied.source is RuleSource.PROJECT_LOCAL


def test_session_rules_override_local_project_and_user_sources(tmp_path: Path) -> None:
    rule_sets = (
        RuleSet(
            RuleSource.PROJECT_LOCAL, (PermissionRule("run_command", "git *", RuleAction.DENY),)
        ),
        RuleSet(RuleSource.PROJECT, (PermissionRule("run_command", "git *", RuleAction.ALLOW),)),
        RuleSet(RuleSource.USER, (PermissionRule("run_command", "git *", RuleAction.DENY),)),
    )
    manager = _manager(tmp_path, rule_sets, confirmation=PermissionConfirmation.SESSION)

    initial = _authorize_command(manager, "git status")
    assert initial.source is RuleSource.PROJECT_LOCAL
    assert initial.allowed is False

    session = _authorize_command(manager, "python -V")
    repeated = _authorize_command(manager, "python -V")
    assert session.allowed is True
    assert session.source is RuleSource.USER_CONFIRMATION
    assert repeated.allowed is True
    assert repeated.source is RuleSource.SESSION


@pytest.mark.parametrize(
    ("mode", "expected_allowed", "expected_source"),
    [
        (PermissionMode.STRICT, False, RuleSource.MODE),
        (PermissionMode.DEFAULT, False, RuleSource.USER_CONFIRMATION),
        (PermissionMode.ALLOW, True, RuleSource.MODE),
    ],
)
def test_unmatched_calls_follow_permission_mode(
    tmp_path: Path, mode: PermissionMode, expected_allowed: bool, expected_source: RuleSource
) -> None:
    manager = _manager(tmp_path, mode=mode)

    decision = _authorize_command(manager, "git status")

    assert decision.allowed is expected_allowed
    assert decision.source is expected_source


def test_exit_confirmation_propagates_to_the_application(tmp_path: Path) -> None:
    manager = _manager(tmp_path, confirmation=PermissionConfirmation.EXIT)

    with pytest.raises(ExitRequested):
        _ = _authorize_command(manager, "git status")


def test_permanent_allow_writes_local_rule_and_outside_path_stays_sandboxed(tmp_path: Path) -> None:
    manager = _manager(tmp_path, confirmation=PermissionConfirmation.PERMANENT)

    allowed = _authorize_command(manager, "git status")
    repeated = _authorize_command(manager, "git status")
    outside = manager.authorize(
        ToolCall("write", "write_file", "{}"),
        _tool("write_file", PermissionTargetKind.PATH),
        {"path": str(tmp_path.parent / "outside.txt")},
    )

    assert allowed.allowed is True
    assert repeated.allowed is True
    assert repeated.source is RuleSource.PROJECT_LOCAL
    assert "git status" in manager.paths.project_local.read_text(encoding="utf-8")
    assert outside.allowed is False
    assert outside.source is RuleSource.SANDBOX
