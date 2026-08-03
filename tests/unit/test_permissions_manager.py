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


def _authorize_path(manager: PermissionManager, tool_name: str, path: str):
    return manager.authorize(
        ToolCall("call", tool_name, "{}"),
        _tool(tool_name, PermissionTargetKind.PATH),
        {"path": path},
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


def test_session_grant_reuses_command_tool_without_overriding_denies(tmp_path: Path) -> None:
    rule_sets = (
        RuleSet(
            RuleSource.PROJECT_LOCAL, (PermissionRule("run_command", "git *", RuleAction.DENY),)
        ),
        RuleSet(RuleSource.PROJECT, (PermissionRule("run_command", "git *", RuleAction.ALLOW),)),
        RuleSet(RuleSource.USER, (PermissionRule("run_command", "git *", RuleAction.DENY),)),
    )
    confirmation_count = 0

    def confirm(_: object) -> PermissionConfirmation:
        nonlocal confirmation_count
        confirmation_count += 1
        return PermissionConfirmation.SESSION

    manager = PermissionManager(
        Workspace(tmp_path),
        rule_sets,
        _paths(tmp_path),
        {"run_command", "write_file"},
        confirmer=confirm,
    )

    initial = _authorize_command(manager, "git status")
    assert initial.source is RuleSource.PROJECT_LOCAL
    assert initial.allowed is False

    session = _authorize_command(manager, "python -V")
    different = _authorize_command(manager, "python --version")
    denied_after_grant = _authorize_command(manager, "git diff")
    blacklisted_after_grant = _authorize_command(manager, "shutdown /r /t 0")

    assert session.allowed is True
    assert session.source is RuleSource.USER_CONFIRMATION
    assert different.allowed is True
    assert different.source is RuleSource.SESSION
    assert denied_after_grant.allowed is False
    assert denied_after_grant.source is RuleSource.PROJECT_LOCAL
    assert blacklisted_after_grant.allowed is False
    assert blacklisted_after_grant.source is RuleSource.BLACKLIST
    assert confirmation_count == 1


def test_session_grant_reuses_path_tool_but_sandbox_still_wins(tmp_path: Path) -> None:
    confirmation_count = 0

    def confirm(_: object) -> PermissionConfirmation:
        nonlocal confirmation_count
        confirmation_count += 1
        return PermissionConfirmation.SESSION

    manager = PermissionManager(
        Workspace(tmp_path),
        (),
        _paths(tmp_path),
        {"write_file"},
        confirmer=confirm,
    )

    first = _authorize_path(manager, "write_file", "src/a.py")
    different = _authorize_path(manager, "write_file", "src/b.py")
    outside = _authorize_path(manager, "write_file", str(tmp_path.parent / "outside.py"))

    assert first.source is RuleSource.USER_CONFIRMATION
    assert different.source is RuleSource.SESSION
    assert outside.allowed is False
    assert outside.source is RuleSource.SANDBOX
    assert confirmation_count == 1
    assert all(
        not path.exists()
        for path in (manager.paths.user, manager.paths.project, manager.paths.project_local)
    )


def test_session_grant_supports_targetless_tools_without_crossing_tools(tmp_path: Path) -> None:
    confirmations = iter((PermissionConfirmation.SESSION, PermissionConfirmation.DENY))
    confirmation_count = 0

    def confirm(_: object) -> PermissionConfirmation:
        nonlocal confirmation_count
        confirmation_count += 1
        return next(confirmations)

    manager = PermissionManager(
        Workspace(tmp_path),
        (),
        _paths(tmp_path),
        {"controlled", "other"},
        confirmer=confirm,
    )
    controlled = _tool("controlled", PermissionTargetKind.NONE)
    other = _tool("other", PermissionTargetKind.NONE)

    first = manager.authorize(ToolCall("1", "controlled", "{}"), controlled, {})
    repeated = manager.authorize(ToolCall("2", "controlled", "{}"), controlled, {})
    isolated = manager.authorize(ToolCall("3", "other", "{}"), other, {})

    assert first.allowed is True
    assert repeated.source is RuleSource.SESSION
    assert isolated.allowed is False
    assert isolated.source is RuleSource.USER_CONFIRMATION
    assert confirmation_count == 2
    assert len(getattr(manager, "_session_grants")) == 1


@pytest.mark.asyncio
async def test_tool_and_hook_session_grants_are_isolated(tmp_path: Path) -> None:
    confirmation_count = 0

    def confirm(_: object) -> PermissionConfirmation:
        nonlocal confirmation_count
        confirmation_count += 1
        return PermissionConfirmation.SESSION

    manager = PermissionManager(
        Workspace(tmp_path),
        (),
        _paths(tmp_path),
        {"run_command"},
        confirmer=confirm,
    )

    tool = _authorize_command(manager, "echo tool")
    background_before_hook_grant = await manager.authorize_hook_command_async(
        "echo background", background=True
    )
    hook = await manager.authorize_hook_command_async("echo hook")
    background_after_hook_grant = await manager.authorize_hook_command_async(
        "echo another", background=True
    )
    dangerous_hook = await manager.authorize_hook_command_async("shutdown /r /t 0", background=True)

    assert tool.allowed is True
    assert background_before_hook_grant.allowed is False
    assert background_before_hook_grant.source is RuleSource.USER_CONFIRMATION
    assert hook.allowed is True
    assert hook.source is RuleSource.USER_CONFIRMATION
    assert background_after_hook_grant.allowed is True
    assert background_after_hook_grant.source is RuleSource.SESSION
    assert dangerous_hook.allowed is False
    assert dangerous_hook.source is RuleSource.BLACKLIST
    assert confirmation_count == 2


def test_once_does_not_reuse_and_session_does_not_persist(tmp_path: Path) -> None:
    confirmations = iter((PermissionConfirmation.ONCE, PermissionConfirmation.SESSION))
    manager = PermissionManager(
        Workspace(tmp_path),
        (),
        _paths(tmp_path),
        {"run_command"},
        confirmer=lambda _: next(confirmations),
    )

    once = _authorize_command(manager, "git status")
    session = _authorize_command(manager, "git diff")
    reused = _authorize_command(manager, "git log -1")

    assert once.source is RuleSource.USER_CONFIRMATION
    assert session.source is RuleSource.USER_CONFIRMATION
    assert reused.source is RuleSource.SESSION
    assert all(
        not path.exists()
        for path in (manager.paths.user, manager.paths.project, manager.paths.project_local)
    )

    fresh = _manager(tmp_path)
    after_rebuild = _authorize_command(fresh, "git log -1")
    assert after_rebuild.allowed is False
    assert after_rebuild.source is RuleSource.USER_CONFIRMATION


def test_permanent_allow_remains_exact_after_manager_rebuild(tmp_path: Path) -> None:
    manager = _manager(tmp_path, confirmation=PermissionConfirmation.PERMANENT)
    permanent = _authorize_command(manager, "git status")

    rebuilt = PermissionManager(
        Workspace(tmp_path),
        (
            RuleSet(
                RuleSource.PROJECT_LOCAL,
                (PermissionRule("run_command", "git status", RuleAction.ALLOW),),
            ),
        ),
        _paths(tmp_path),
        {"run_command"},
        confirmer=lambda _: PermissionConfirmation.DENY,
    )
    exact = _authorize_command(rebuilt, "git status")
    different = _authorize_command(rebuilt, "git diff")

    assert permanent.allowed is True
    assert exact.allowed is True
    assert exact.source is RuleSource.PROJECT_LOCAL
    assert different.allowed is False
    assert different.source is RuleSource.USER_CONFIRMATION


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
