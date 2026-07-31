"""Hook YAML 配置加载和集中校验。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from urllib.parse import urlparse

import yaml

from okcode.errors import ConfigError
from okcode.hooks.models import (
    ConditionMode,
    HookAction,
    HookCondition,
    HookConditionGroup,
    HookControl,
    HookEvent,
    HookRule,
    HttpHookAction,
    PromptHookAction,
    PromptScope,
    ShellHookAction,
    SubAgentHookAction,
)
from okcode.matching import parse_match_expression

_ROOT_FIELDS = {"hooks"}
_RULE_FIELDS = {"name", "enabled", "event", "if", "action", "control"}
_CONDITION_FIELDS = {"field", "match"}
_CONTROL_FIELDS = {"once", "background", "timeout_seconds"}
_ACTION_COMMON = {"type"}
_SHELL_FIELDS = {*_ACTION_COMMON, "command", "cwd", "intercept", "deny_message"}
_PROMPT_FIELDS = {*_ACTION_COMMON, "content", "scope"}
_HTTP_FIELDS = {*_ACTION_COMMON, "url", "method", "headers", "body"}
_SUBAGENT_FIELDS = {*_ACTION_COMMON, "task", "profile"}

_EVENT_FIELDS: dict[HookEvent, tuple[str, ...]] = {
    HookEvent.SESSION_START: ("session.id", "runtime.mode"),
    HookEvent.SESSION_END: ("session.id", "session.turn_count"),
    HookEvent.TURN_START: ("turn.kind", "turn.index", "runtime.mode", "message.content"),
    HookEvent.TURN_END: ("turn.kind", "turn.index", "turn.outcome"),
    HookEvent.MESSAGE_USER: ("message.content", "runtime.mode"),
    HookEvent.MESSAGE_ASSISTANT: (
        "message.content",
        "message.tool_call_count",
        "runtime.mode",
    ),
    HookEvent.TOOL_BEFORE: ("tool.name", "tool.safety", "tool.target", "tool.arguments."),
    HookEvent.TOOL_AFTER: (
        "tool.name",
        "tool.arguments.",
        "tool.result.success",
        "tool.result.error_code",
        "tool.result.truncated",
    ),
    HookEvent.CONTEXT_COMPACTED: ("context.reason", "context.summary_length"),
    HookEvent.ERROR: ("error.category", "error.message"),
}


@dataclass(frozen=True, slots=True)
class HookPaths:
    """Hook 配置文件路径。"""

    config: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> HookPaths:
        return cls(workspace_root / ".okcode" / "hooks.yaml")


def load_hook_rules(paths: HookPaths) -> tuple[HookRule, ...]:
    """加载并校验工作区 Hook 配置。"""

    path = paths.config
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise ConfigError(f"无法读取 Hook 配置：{path}") from exc

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Hook 配置 YAML 语法错误：{path}") from exc
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise ConfigError(f"Hook 配置根节点必须是对象：{path}")
    _reject_unknown(raw, _ROOT_FIELDS, f"{path}")
    hooks = raw.get("hooks", ())
    if not isinstance(hooks, list):
        raise ConfigError(f"Hook 配置 hooks 必须是列表：{path}")
    return tuple(_parse_rule(path, index, item) for index, item in enumerate(hooks))


def _parse_rule(path: Path, index: int, raw: object) -> HookRule:
    location = f"{path} 的 hooks[{index}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{location} 必须是对象。")
    _reject_unknown(raw, _RULE_FIELDS, location)
    if "event" not in raw or "action" not in raw:
        raise ConfigError(f"{location} 必须包含 event 和 action。")
    identifier = _optional_string(raw.get("name"), f"{location}.name") or f"#{index + 1}"
    enabled = _optional_bool(raw.get("enabled"), f"{location}.enabled", True)
    try:
        event = HookEvent(_required_string(raw["event"], f"{location}.event"))
    except ValueError as exc:
        raise ConfigError(f"{location}.event 未知：{raw['event']!r}") from exc
    conditions = _parse_conditions(path, index, event, raw.get("if"))
    action = _parse_action(path, index, event, raw["action"])
    control = _parse_control(path, index, raw.get("control"))
    if isinstance(action, ShellHookAction) and action.intercept:
        if event is not HookEvent.TOOL_BEFORE:
            raise ConfigError(f"{location}.action.intercept 只能用于 tool.before。")
        if control.background:
            raise ConfigError(f"{location}.control.background 不能用于拦截类 Hook。")
    return HookRule(identifier, event, conditions, action, control, enabled)


def _parse_conditions(
    path: Path,
    index: int,
    event: HookEvent,
    raw: object,
) -> HookConditionGroup | None:
    location = f"{path} 的 hooks[{index}].if"
    if raw is None:
        return None
    if isinstance(raw, list):
        conditions = _parse_condition_list(path, index, event, raw, location)
        return HookConditionGroup(ConditionMode.ALL, conditions)
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{location} 必须是列表，或只包含 all/any 的对象。")
    keys = set(raw)
    if keys not in ({"all"}, {"any"}):
        raise ConfigError(f"{location} 只能二选一包含 all 或 any。")
    key = next(iter(keys))
    if not isinstance(raw[key], list):
        raise ConfigError(f"{location}.{key} 必须是条件列表。")
    conditions = _parse_condition_list(path, index, event, raw[key], f"{location}.{key}")
    return HookConditionGroup(ConditionMode(key), conditions)


def _parse_condition_list(
    path: Path,
    index: int,
    event: HookEvent,
    raw: list[object],
    location: str,
) -> tuple[HookCondition, ...]:
    if not raw:
        raise ConfigError(f"{location} 不能为空。")
    return tuple(
        _parse_condition(path, index, event, item, i, location) for i, item in enumerate(raw)
    )


def _parse_condition(
    path: Path,
    index: int,
    event: HookEvent,
    raw: object,
    condition_index: int,
    parent: str,
) -> HookCondition:
    location = f"{parent}[{condition_index}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{location} 必须是对象。")
    _reject_unknown(raw, _CONDITION_FIELDS, location)
    if set(raw) != _CONDITION_FIELDS:
        raise ConfigError(f"{location} 必须包含 field 和 match。")
    field = _required_string(raw["field"], f"{location}.field")
    if not _field_allowed(event, field):
        raise ConfigError(f"{location}.field 不属于事件 {event.value}。")
    try:
        expression = parse_match_expression(raw["match"], f"{location}.match")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return HookCondition(field, expression)


def _parse_action(path: Path, index: int, event: HookEvent, raw: object) -> HookAction:
    location = f"{path} 的 hooks[{index}].action"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{location} 必须是对象。")
    action_type = _required_string(raw.get("type"), f"{location}.type")
    if action_type == "shell":
        _reject_unknown(raw, _SHELL_FIELDS, location)
        command = _required_string(raw.get("command"), f"{location}.command")
        cwd = _optional_string(raw.get("cwd"), f"{location}.cwd")
        if cwd is not None:
            _validate_relative_cwd(cwd, f"{location}.cwd")
        intercept = _optional_bool(raw.get("intercept"), f"{location}.intercept", False)
        deny_message = _optional_string(raw.get("deny_message"), f"{location}.deny_message")
        return ShellHookAction(command, cwd, intercept, deny_message)
    if action_type == "prompt":
        _reject_unknown(raw, _PROMPT_FIELDS, location)
        content = _required_string(raw.get("content"), f"{location}.content")
        try:
            scope = PromptScope(
                _optional_string(raw.get("scope"), f"{location}.scope") or "next_request"
            )
        except ValueError as exc:
            raise ConfigError(f"{location}.scope 只能是 next_request、turn 或 session。") from exc
        return PromptHookAction(content, scope)
    if action_type == "http":
        _reject_unknown(raw, _HTTP_FIELDS, location)
        url = _required_string(raw.get("url"), f"{location}.url")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError(f"{location}.url 必须是 http 或 https URL。")
        method = (_optional_string(raw.get("method"), f"{location}.method") or "POST").upper()
        headers = _headers(raw.get("headers"), f"{location}.headers")
        return HttpHookAction(url, method, headers, raw.get("body"))
    if action_type == "subagent":
        _reject_unknown(raw, _SUBAGENT_FIELDS, location)
        task = _required_string(raw.get("task"), f"{location}.task")
        profile = _optional_string(raw.get("profile"), f"{location}.profile")
        return SubAgentHookAction(task, profile)
    raise ConfigError(f"{location}.type 只能是 shell、prompt、http 或 subagent。")


def _parse_control(path: Path, index: int, raw: object) -> HookControl:
    location = f"{path} 的 hooks[{index}].control"
    if raw is None:
        return HookControl()
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{location} 必须是对象。")
    _reject_unknown(raw, _CONTROL_FIELDS, location)
    once = _optional_bool(raw.get("once"), f"{location}.once", False)
    background = _optional_bool(raw.get("background"), f"{location}.background", False)
    timeout = _optional_number(raw.get("timeout_seconds"), f"{location}.timeout_seconds", 10.0)
    if timeout <= 0 or not math.isfinite(timeout):
        raise ConfigError(f"{location}.timeout_seconds 必须是有限正数。")
    return HookControl(once, background, timeout)


def _field_allowed(event: HookEvent, field: str) -> bool:
    for allowed in _EVENT_FIELDS[event]:
        if allowed.endswith("."):
            if field.startswith(allowed) and field != allowed:
                return True
            continue
        if field == allowed:
            return True
    return False


def _reject_unknown(value: Mapping[object, object], allowed: set[str], location: str) -> None:
    unknown = {item for item in value if item not in allowed}
    if unknown:
        raise ConfigError(f"{location} 包含未知字段：{', '.join(sorted(str(x) for x in unknown))}")


def _required_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} 必须是非空字符串。")
    return value.strip()


def _optional_string(value: object, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} 必须是非空字符串。")
    return value.strip()


def _optional_bool(value: object, location: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{location} 必须是布尔值。")
    return value


def _optional_number(value: object, location: str, default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, int | float):
        raise ConfigError(f"{location} 必须是数字。")
    return float(value)


def _headers(value: object, location: str) -> Mapping[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{location} 必须是对象。")
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ConfigError(f"{location} 的键和值都必须是字符串。")
    return dict(value)


def _validate_relative_cwd(value: str, location: str) -> None:
    windows = PureWindowsPath(value)
    pure = PurePath(value)
    if windows.is_absolute() or pure.is_absolute():
        raise ConfigError(f"{location} 必须是工作区内相对路径。")
    if any(part == ".." for part in windows.parts) or any(part == ".." for part in pure.parts):
        raise ConfigError(f"{location} 不能包含 ..。")
