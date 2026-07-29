from __future__ import annotations

from pathlib import Path

import pytest

from okcode.errors import ConfigError
from okcode.permissions.models import PermissionRule, RuleAction, parse_rule_text
from okcode.permissions.rules import PermissionPaths, append_local_allow_rule, load_permission_rules


def _paths(root: Path) -> PermissionPaths:
    return PermissionPaths(
        user=root / "user.yaml",
        project=root / ".okcode" / "permissions.yaml",
        project_local=root / ".okcode" / "permissions.local.yaml",
    )


def test_rule_parser_supports_bash_alias_and_rejects_invalid_text() -> None:
    known = {"run_command", "write_file"}

    assert parse_rule_text("Bash(git *)", known) == ("run_command", "git *")
    assert parse_rule_text("write_file", known) == ("write_file", None)
    for invalid in ("", "missing(*)", "run_command(", "run_command()", "Bash([abc)", 1):
        with pytest.raises(ValueError):
            parse_rule_text(invalid, known)


def test_load_rules_keeps_source_order_and_reports_file_location(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.project.parent.mkdir()
    paths.project.write_text(
        "rules:\n"
        "  - match: Bash(git *)\n"
        "    action: allow\n"
        "  - match: write_file(.env)\n"
        "    action: deny\n",
        encoding="utf-8",
    )

    rule_sets = load_permission_rules(paths, {"run_command", "write_file"})

    assert [item.source.value for item in rule_sets] == ["project_local", "project", "user"]
    assert [rule.to_text() for rule in rule_sets[1].rules] == [
        "run_command(git *)",
        "write_file(.env)",
    ]

    paths.user.write_text("rules: nope\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="user.yaml"):
        load_permission_rules(paths, {"run_command", "write_file"})


def test_append_local_allow_rule_creates_and_preserves_rules(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    known = {"run_command"}
    first = PermissionRule("run_command", "git status", RuleAction.ALLOW)
    second = PermissionRule("run_command", "git diff", RuleAction.ALLOW)

    append_local_allow_rule(paths, first, known)
    append_local_allow_rule(paths, second, known)

    loaded = load_permission_rules(paths, known)[0]
    assert [rule.to_text() for rule in loaded.rules] == [
        "run_command(git status)",
        "run_command(git diff)",
    ]
    assert all(rule.action is RuleAction.ALLOW for rule in loaded.rules)
