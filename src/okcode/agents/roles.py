"""子 Agent 角色文件发现、解析和覆盖规则。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from okcode.agents.models import (
    AgentModelKind,
    AgentModelPolicy,
    AgentPermissionKind,
    AgentPermissionPolicy,
    AgentRole,
    AgentRoleCatalog,
    AgentRoleSourceKind,
    ShadowedAgentRole,
)
from okcode.errors import ConfigError
from okcode.permissions.models import PermissionMode

_ROOT_FIELDS = {"name", "description", "tools", "model", "max_turns", "permission"}
_TOOLS_FIELDS = {"allow", "deny"}


@dataclass(frozen=True, slots=True)
class AgentRolePaths:
    """四层角色根目录。"""

    plugin: tuple[Path, ...]
    builtin: Path
    user: Path
    project: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> AgentRolePaths:
        package_root = Path(__file__).resolve().parent
        return cls(
            plugin=(),
            builtin=package_root / "builtin_roles",
            user=Path.home() / ".okcode" / "agents",
            project=workspace_root / ".okcode" / "agents",
        )


def load_agent_roles(paths: AgentRolePaths) -> AgentRoleCatalog:
    """加载所有角色，并按来源优先级应用同名覆盖。"""

    discovered: list[AgentRole] = []
    for source, root in _root_items(paths):
        for path in _markdown_files(root):
            discovered.append(parse_agent_role_markdown(path, source))

    selected: dict[str, AgentRole] = {}
    shadowed: list[ShadowedAgentRole] = []
    for role in sorted(discovered, key=lambda value: (value.source_kind.priority, value.name)):
        previous = selected.get(role.name)
        if previous is not None:
            shadowed.append(ShadowedAgentRole(role.name, previous, role))
        selected[role.name] = role
    return AgentRoleCatalog(dict(sorted(selected.items())), tuple(shadowed))


def parse_agent_role_markdown(path: Path, source: AgentRoleSourceKind) -> AgentRole:
    """解析单个 Markdown 角色定义。"""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"无法读取子 Agent 角色文件：{path}") from exc
    frontmatter, body = _split_frontmatter(text, path)
    data = _load_yaml(frontmatter, path)
    _reject_unknown(data, _ROOT_FIELDS, f"{path} frontmatter")
    role_name = _string(data, "name", path)
    system_prompt = body.strip()
    if not system_prompt:
        raise ConfigError(f"子 Agent 角色 {role_name!r} 正文不能为空：{path}")
    allow, deny = _tools(data.get("tools"), path, role_name)
    overlap = sorted(set(allow) & set(deny))
    if overlap:
        raise ConfigError(
            f"子 Agent 角色 {role_name!r} 的 tools.allow 与 tools.deny 冲突："
            f"{', '.join(overlap)}：{path}"
        )
    max_turns = _positive_int(data.get("max_turns", 6), "max_turns", path, role_name)
    return AgentRole(
        name=role_name,
        description=_string(data, "description", path),
        source_kind=source,
        source_path=path,
        tool_allowlist=allow,
        tool_denylist=deny,
        model_policy=AgentModelPolicy(
            _enum(data.get("model", "inherit"), AgentModelKind, path, role_name, "model")
        ),
        max_turns=max_turns,
        permission_policy=_permission_policy(data.get("permission", "inherit"), path, role_name),
        system_prompt=system_prompt,
    )


def _root_items(paths: AgentRolePaths) -> tuple[tuple[AgentRoleSourceKind, Path], ...]:
    plugin_roots = tuple((AgentRoleSourceKind.PLUGIN, root) for root in paths.plugin)
    return (
        *plugin_roots,
        (AgentRoleSourceKind.BUILTIN, paths.builtin),
        (AgentRoleSourceKind.USER, paths.user),
        (AgentRoleSourceKind.PROJECT, paths.project),
    )


def _markdown_files(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    if not root.is_dir():
        raise ConfigError(f"子 Agent 角色路径必须是目录：{root}")
    return tuple(sorted(root.glob("*.md"), key=lambda value: value.name.casefold()))


def _split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ConfigError(f"子 Agent 角色缺少 YAML frontmatter：{path}")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise ConfigError(f"子 Agent 角色缺少 YAML frontmatter 结束标记：{path}")


def _load_yaml(frontmatter: str, path: Path) -> Mapping[str, object]:
    try:
        raw = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise ConfigError(f"子 Agent 角色 YAML 语法错误：{path}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"子 Agent 角色 frontmatter 必须是对象：{path}")
    return raw


def _reject_unknown(data: Mapping[object, object], allowed: set[str], location: str) -> None:
    unknown = sorted(str(item) for item in data if item not in allowed)
    if unknown:
        raise ConfigError(f"{location} 包含未知字段：{', '.join(unknown)}")


def _string(data: Mapping[str, object], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"子 Agent 角色字段 {key} 必须是非空字符串：{path}")
    return value.strip()


def _string_tuple(value: object, location: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{location} 必须是字符串列表。")
    return tuple(item.strip() for item in value if item.strip())


def _tools(value: object, path: Path, role_name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if value is None:
        return (), ()
    if not isinstance(value, Mapping):
        raise ConfigError(f"子 Agent 角色 {role_name!r} 字段 tools 必须是对象：{path}")
    _reject_unknown(value, _TOOLS_FIELDS, f"{path} 的角色 {role_name!r}.tools")
    return (
        _string_tuple(value.get("allow"), f"{path} 的角色 {role_name!r}.tools.allow"),
        _string_tuple(value.get("deny"), f"{path} 的角色 {role_name!r}.tools.deny"),
    )


def _enum(
    value: object,
    enum_type: type[AgentModelKind],
    path: Path,
    role_name: str,
    field: str,
) -> AgentModelKind:
    if not isinstance(value, str):
        raise ConfigError(f"子 Agent 角色 {role_name!r} 字段 {field} 必须是字符串：{path}")
    try:
        return enum_type(value.strip())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ConfigError(
            f"子 Agent 角色 {role_name!r} 字段 {field} 只能是：{allowed}：{path}"
        ) from exc


def _permission_policy(value: object, path: Path, role_name: str) -> AgentPermissionPolicy:
    if not isinstance(value, str):
        raise ConfigError(f"子 Agent 角色 {role_name!r} 字段 permission 必须是字符串：{path}")
    try:
        kind = AgentPermissionKind(value.strip())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in AgentPermissionKind)
        raise ConfigError(
            f"子 Agent 角色 {role_name!r} 字段 permission 只能是：{allowed}：{path}"
        ) from exc
    resolved = {
        AgentPermissionKind.DEFAULT: PermissionMode.DEFAULT,
        AgentPermissionKind.STRICT: PermissionMode.STRICT,
        AgentPermissionKind.ALLOW: PermissionMode.ALLOW,
        AgentPermissionKind.INHERIT: None,
    }[kind]
    return AgentPermissionPolicy(kind, resolved)


def _positive_int(value: object, field: str, path: Path, role_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"子 Agent 角色 {role_name!r} 字段 {field} 必须是正整数：{path}")
    return value


def role_names(roles: Iterable[AgentRole]) -> tuple[str, ...]:
    """返回稳定排序后的角色名。"""

    return tuple(sorted(role.name for role in roles))
