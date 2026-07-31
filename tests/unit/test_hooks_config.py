from __future__ import annotations

from pathlib import Path

import pytest

from okcode.errors import ConfigError
from okcode.hooks.config import HookPaths, load_hook_rules
from okcode.hooks.models import (
    ConditionMode,
    HookEvent,
    HttpHookAction,
    PromptHookAction,
    PromptScope,
    ShellHookAction,
    SubAgentHookAction,
)


def _paths(root: Path) -> HookPaths:
    return HookPaths(root / ".okcode" / "hooks.yaml")


def _write(root: Path, content: str) -> HookPaths:
    paths = _paths(root)
    paths.config.parent.mkdir(parents=True)
    paths.config.write_text(content, encoding="utf-8")
    return paths


def test_missing_config_loads_empty_rules(tmp_path: Path) -> None:
    assert load_hook_rules(_paths(tmp_path)) == ()


def test_loads_valid_rules_with_defaults(tmp_path: Path) -> None:
    paths = _write(
        tmp_path,
        """
hooks:
  - name: inject
    event: message.user
    if:
      - field: message.content
        match: glob:*总结*
    action:
      type: prompt
      content: 请用中文回答。
""",
    )

    (rule,) = load_hook_rules(paths)

    assert rule.identifier == "inject"
    assert rule.event is HookEvent.MESSAGE_USER
    assert rule.enabled is True
    assert rule.control.timeout_seconds == 10
    assert rule.conditions is not None
    assert rule.conditions.mode is ConditionMode.ALL
    assert isinstance(rule.action, PromptHookAction)
    assert rule.action.scope is PromptScope.NEXT_REQUEST


def test_rejects_invalid_root_and_required_fields(tmp_path: Path) -> None:
    paths = _write(tmp_path, "hooks:\n  - event: message.user\n")

    with pytest.raises(ConfigError, match=r"hooks\[0\].*event 和 action"):
        load_hook_rules(paths)

    paths.config.write_text("hooks: nope\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="hooks 必须是列表"):
        load_hook_rules(paths)


def test_condition_modes_and_field_validation(tmp_path: Path) -> None:
    paths = _write(
        tmp_path,
        """
hooks:
  - event: tool.before
    if:
      any:
        - field: tool.name
          match: exact:write_file
        - field: tool.arguments.path
          match: regex:.*\\.py$
    action:
      type: shell
      command: exit 0
""",
    )

    (rule,) = load_hook_rules(paths)
    assert rule.conditions is not None
    assert rule.conditions.mode is ConditionMode.ANY
    assert isinstance(rule.action, ShellHookAction)

    paths.config.write_text(
        """
hooks:
  - event: message.user
    if:
      - field: tool.name
        match: exact:write_file
    action:
      type: prompt
      content: x
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="不属于事件"):
        load_hook_rules(paths)


def test_action_and_control_validation(tmp_path: Path) -> None:
    paths = _write(
        tmp_path,
        """
hooks:
  - event: tool.before
    action:
      type: shell
      command: exit 1
      intercept: true
      deny_message: 拒绝
    control:
      timeout_seconds: 5
""",
    )

    (rule,) = load_hook_rules(paths)
    assert isinstance(rule.action, ShellHookAction)
    assert rule.action.intercept is True

    paths.config.write_text(
        """
hooks:
  - event: tool.before
    action:
      type: shell
      command: exit 1
      intercept: true
    control:
      background: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="background"):
        load_hook_rules(paths)


def test_http_and_subagent_actions(tmp_path: Path) -> None:
    paths = _write(
        tmp_path,
        """
hooks:
  - event: session.start
    action:
      type: http
      url: https://example.com/hook
      method: post
      headers:
        X-Test: ok
      body:
        hello: world
  - event: session.end
    action:
      type: subagent
      task: 收尾检查
      profile: default
""",
    )

    http_rule, subagent_rule = load_hook_rules(paths)

    assert isinstance(http_rule.action, HttpHookAction)
    assert http_rule.action.method == "POST"
    assert http_rule.action.headers["X-Test"] == "ok"
    assert isinstance(subagent_rule.action, SubAgentHookAction)
    assert subagent_rule.action.task == "收尾检查"
