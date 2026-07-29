"""权限 YAML 规则的加载、优先级和项目本地持久化。"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from okcode.errors import ConfigError
from okcode.permissions.models import (
    PermissionRule,
    RuleAction,
    RuleSet,
    RuleSource,
    parse_rule_text,
)

_RULES_FIELD = "rules"
_RULE_FIELDS = {"match", "action"}


@dataclass(frozen=True, slots=True)
class PermissionPaths:
    """用户、项目和项目本地权限规则的固定位置。"""

    user: Path
    project: Path
    project_local: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> PermissionPaths:
        permissions_dir = workspace_root / ".okcode"
        return cls(
            user=Path.home() / ".okcode" / "permissions.yaml",
            project=permissions_dir / "permissions.yaml",
            project_local=permissions_dir / "permissions.local.yaml",
        )

    def path_for(self, source: RuleSource) -> Path:
        paths = {
            RuleSource.USER: self.user,
            RuleSource.PROJECT: self.project,
            RuleSource.PROJECT_LOCAL: self.project_local,
        }
        try:
            return paths[source]
        except KeyError as exc:
            raise ValueError(f"{source} 没有对应的 YAML 规则文件。") from exc


def load_permission_rules(
    paths: PermissionPaths, known_tool_names: set[str]
) -> tuple[RuleSet, ...]:
    """加载持久规则，并按运行时优先级从高到低返回。"""

    return tuple(
        RuleSet(source, _load_rule_file(paths.path_for(source), known_tool_names))
        for source in (RuleSource.PROJECT_LOCAL, RuleSource.PROJECT, RuleSource.USER)
    )


def append_local_allow_rule(
    paths: PermissionPaths,
    rule: PermissionRule,
    known_tool_names: set[str],
) -> None:
    """安全追加一条永久允许规则，失败时不改变当前调用的权限结果。"""

    path = paths.project_local
    existing = _load_rule_file(path, known_tool_names)
    content = {
        _RULES_FIELD: [
            {"match": item.to_text(), "action": item.action.value} for item in (*existing, rule)
        ]
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_yaml(path, content)
    except OSError as exc:
        raise ConfigError(f"无法写入项目本地权限规则：{path}") from exc


def _load_rule_file(path: Path, known_tool_names: set[str]) -> tuple[PermissionRule, ...]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise ConfigError(f"无法读取权限规则文件：{path}") from exc

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigError(f"权限规则 YAML 语法错误：{path}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"权限规则根节点必须是对象：{path}")
    unknown = set(raw) - {_RULES_FIELD}
    if unknown:
        raise ConfigError(f"权限规则包含未知字段：{path}：{', '.join(sorted(unknown))}")
    entries = raw.get(_RULES_FIELD)
    if not isinstance(entries, list):
        raise ConfigError(f"权限规则的 rules 必须是列表：{path}")

    rules: list[PermissionRule] = []
    for index, entry in enumerate(entries):
        location = f"{path} 的 rules[{index}]"
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{location} 必须是对象。")
        unknown_fields = set(entry) - _RULE_FIELDS
        if unknown_fields:
            raise ConfigError(f"{location} 包含未知字段：{', '.join(sorted(unknown_fields))}")
        if set(entry) != _RULE_FIELDS:
            raise ConfigError(f"{location} 必须包含 match 和 action。")
        try:
            tool_name, pattern = parse_rule_text(entry.get("match"), known_tool_names)
        except ValueError as exc:
            raise ConfigError(f"{location}.match 无效：{exc}") from exc
        try:
            action = RuleAction(entry.get("action"))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{location}.action 只能是 allow 或 deny。") from exc
        rules.append(PermissionRule(tool_name, pattern, action))
    return tuple(rules)


def _atomic_write_yaml(path: Path, value: Mapping[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".okcode-permissions-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            yaml.safe_dump(value, file, allow_unicode=True, sort_keys=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
