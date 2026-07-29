"""YAML 配置加载与严格校验。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import yaml

from okcode.errors import ConfigError
from okcode.models import AppConfig, ProviderConfig, ProviderProtocol

_ROOT_FIELDS = {"active", "providers"}
_PROVIDER_FIELDS = {"name", "protocol", "model", "base_url", "api_key", "thinking", "prompt_cache"}
_REQUIRED_PROVIDER_FIELDS = _PROVIDER_FIELDS - {"thinking", "prompt_cache"}


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
    return AppConfig(active=active, providers=providers)


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
