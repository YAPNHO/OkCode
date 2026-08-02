"""YAML 配置加载与严格校验。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import yaml

from okcode.errors import ConfigError
from okcode.models import AppConfig, ProviderConfig, ProviderProtocol, TeamFeatureConfig

_ROOT_FIELDS = {"active", "providers", "team"}
_PROVIDER_FIELDS = {"name", "protocol", "model", "base_url", "api_key", "thinking", "prompt_cache"}
_REQUIRED_PROVIDER_FIELDS = _PROVIDER_FIELDS - {"thinking", "prompt_cache"}
_TEAM_FIELDS = {
    "coordinator_enabled",
    "teams_root",
    "terminal_backend_priority",
    "mailbox_lock_timeout_seconds",
    "mailbox_stale_lock_seconds",
}


def default_config_path() -> Path:
    """返回当前工作目录中的默认配置路径。"""

    return Path.cwd() / "config.yaml"


def load_config(path: Path | None = None) -> AppConfig:
    """安全加载并校验 OkCode YAML 配置。"""

    config_path = path or default_config_path()
    try:
        content = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"未找到配置文件：{config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{config_path}") from exc

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        position = ""
        if mark is not None:
            position = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）"
        raise ConfigError(f"YAML 语法错误{position}") from exc

    root = _mapping(raw, "根节点")
    _reject_unknown_fields(root, _ROOT_FIELDS, "根节点")
    active = _nonempty_string(root.get("active"), "active")
    providers_raw = root.get("providers")
    if not isinstance(providers_raw, list) or not providers_raw:
        raise ConfigError("providers 必须是非空列表")

    providers = tuple(_parse_provider(item, index) for index, item in enumerate(providers_raw))
    names = [provider.name for provider in providers]
    if len(names) != len(set(names)):
        raise ConfigError("providers 中的 name 不能重复")
    if active not in names:
        raise ConfigError(f"active 引用了不存在的供应商配置：{active}")
    team = _parse_team(root.get("team", {}))
    return AppConfig(active=active, providers=providers, team=team)


def _parse_provider(raw: object, index: int) -> ProviderConfig:
    location = f"providers[{index}]"
    item = _mapping(raw, location)
    _reject_unknown_fields(item, _PROVIDER_FIELDS, location)
    missing = _REQUIRED_PROVIDER_FIELDS - item.keys()
    if missing:
        raise ConfigError(f"{location} 缺少字段：{', '.join(sorted(missing))}")

    protocol_text = _nonempty_string(item.get("protocol"), f"{location}.protocol")
    try:
        protocol = ProviderProtocol(protocol_text)
    except ValueError as exc:
        allowed = ", ".join(protocol.value for protocol in ProviderProtocol)
        raise ConfigError(f"{location}.protocol 只能是：{allowed}") from exc

    thinking = item.get("thinking", False)
    if type(thinking) is not bool:
        raise ConfigError(f"{location}.thinking 必须是布尔值")

    prompt_cache = item.get("prompt_cache", False)
    if type(prompt_cache) is not bool:
        raise ConfigError(f"{location}.prompt_cache 必须是布尔值")

    base_url = _nonempty_string(item.get("base_url"), f"{location}.base_url")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{location}.base_url 必须是有效的 HTTP(S) 地址")

    return ProviderConfig(
        name=_nonempty_string(item.get("name"), f"{location}.name"),
        protocol=protocol,
        model=_nonempty_string(item.get("model"), f"{location}.model"),
        base_url=base_url,
        api_key=_nonempty_string(item.get("api_key"), f"{location}.api_key"),
        thinking=thinking,
        prompt_cache=prompt_cache,
    )


def _parse_team(raw: object) -> TeamFeatureConfig:
    item = _mapping(raw, "team")
    _reject_unknown_fields(item, _TEAM_FIELDS, "team")
    coordinator_enabled = item.get("coordinator_enabled", False)
    if type(coordinator_enabled) is not bool:
        raise ConfigError("team.coordinator_enabled 必须是布尔值")
    teams_root_raw = item.get("teams_root")
    teams_root = None
    if teams_root_raw is not None:
        teams_root = Path(_nonempty_string(teams_root_raw, "team.teams_root"))
    priority_raw = item.get("terminal_backend_priority", ("windows_terminal", "tmux"))
    if isinstance(priority_raw, list) and all(
        isinstance(value, str) and value for value in priority_raw
    ):
        priority = tuple(priority_raw)
    elif isinstance(priority_raw, tuple):
        priority = priority_raw
    else:
        raise ConfigError("team.terminal_backend_priority 必须是字符串列表")
    lock_timeout = _positive_float(
        item.get("mailbox_lock_timeout_seconds", 5.0),
        "team.mailbox_lock_timeout_seconds",
    )
    stale_lock = _positive_float(
        item.get("mailbox_stale_lock_seconds", 30.0),
        "team.mailbox_stale_lock_seconds",
    )
    return TeamFeatureConfig(
        coordinator_enabled=coordinator_enabled,
        teams_root=teams_root,
        terminal_backend_priority=priority,
        mailbox_lock_timeout_seconds=lock_timeout,
        mailbox_stale_lock_seconds=stale_lock,
    )


def _mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{location} 必须是对象")
    if not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{location} 的字段名必须是字符串")
    return value


def _reject_unknown_fields(value: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{location} 包含未知字段：{', '.join(sorted(unknown))}")


def _nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} 必须是非空字符串")
    return value.strip()


def _positive_float(value: object, location: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{location} 必须是正数")
    return float(value)
