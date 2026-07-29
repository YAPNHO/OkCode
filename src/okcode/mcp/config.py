"""分层 MCP YAML 配置的读取、校验和环境变量展开。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import yaml

from okcode.errors import ConfigError
from okcode.mcp.models import (
    McpConfig,
    McpConfigPaths,
    McpServerConfig,
    McpTransport,
    StdioMcpServerConfig,
    StreamableHttpMcpServerConfig,
)

_ROOT_FIELDS = {"mcp_servers"}
_STDIO_FIELDS = {"transport", "command", "args", "env"}
_HTTP_FIELDS = {"transport", "url", "headers"}
_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_mcp_config(
    paths: McpConfigPaths,
    *,
    environ: Mapping[str, str] | None = None,
) -> McpConfig:
    """读取用户级和项目级配置，并以项目级覆盖同名 Server。"""

    variables = os.environ if environ is None else environ
    user_servers = _load_server_mapping(paths.user)
    project_servers = _load_server_mapping(paths.project)
    merged = dict(user_servers)
    merged.update(project_servers)
    servers = tuple(
        _parse_server(
            name,
            raw,
            paths.project if name in project_servers else paths.user,
            variables,
        )
        for name, raw in sorted(merged.items())
    )
    return McpConfig(servers)


def stdio_environment(
    config: StdioMcpServerConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """生成继承系统环境、并由配置覆盖的 stdio 子进程环境。"""

    result = dict(os.environ if environ is None else environ)
    result.update(config.env)
    return result


def _load_server_mapping(path: Path) -> dict[str, object]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"无法读取 MCP 配置：{path}") from exc

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigError(f"MCP 配置 YAML 语法错误：{path}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"MCP 配置根节点必须是对象：{path}")
    if not all(isinstance(key, str) for key in raw):
        raise ConfigError(f"MCP 配置根节点字段名必须是字符串：{path}")
    unknown = set(raw) - _ROOT_FIELDS
    if unknown:
        raise ConfigError(f"MCP 配置包含未知字段：{path}：{', '.join(sorted(unknown))}")
    servers = raw.get("mcp_servers", {})
    if not isinstance(servers, Mapping):
        raise ConfigError(f"MCP 配置的 mcp_servers 必须是对象：{path}")
    result: dict[str, object] = {}
    for name, value in servers.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"MCP 配置的 Server 名称必须是非空字符串：{path}")
        result[name.strip()] = value
    return result


def _parse_server(
    name: str,
    raw: object,
    source: Path,
    environ: Mapping[str, str],
) -> McpServerConfig:
    location = f"{source} 的 mcp_servers.{name}"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{location} 必须是对象")
    if not all(isinstance(key, str) for key in raw):
        raise ConfigError(f"{location} 的字段名必须是字符串")
    transport_text = _nonempty_string(raw.get("transport"), f"{location}.transport")
    try:
        transport = McpTransport(transport_text)
    except ValueError as exc:
        allowed = "、".join(item.value for item in McpTransport)
        raise ConfigError(f"{location}.transport 只能是：{allowed}") from exc

    if transport is McpTransport.STDIO:
        _reject_unknown(raw, _STDIO_FIELDS, location)
        return StdioMcpServerConfig(
            name=name,
            command=_expand_nonempty(raw.get("command"), f"{location}.command", environ),
            args=_string_list(raw.get("args", []), f"{location}.args", environ),
            env=_string_mapping(raw.get("env", {}), f"{location}.env", environ),
        )

    _reject_unknown(raw, _HTTP_FIELDS, location)
    url = _expand_nonempty(raw.get("url"), f"{location}.url", environ)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{location}.url 必须是有效的 HTTP(S) 地址")
    return StreamableHttpMcpServerConfig(
        name=name,
        url=url,
        headers=_string_mapping(raw.get("headers", {}), f"{location}.headers", environ),
    )


def _reject_unknown(value: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{location} 包含未知字段：{', '.join(sorted(unknown))}")


def _nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} 必须是非空字符串")
    return value.strip()


def _expand_nonempty(value: object, location: str, environ: Mapping[str, str]) -> str:
    return _expand(_nonempty_string(value, location), location, environ).strip()


def _string_list(value: object, location: str, environ: Mapping[str, str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{location} 必须是字符串列表")
    return tuple(_expand(item, f"{location}[{index}]", environ) for index, item in enumerate(value))


def _string_mapping(value: object, location: str, environ: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{location} 必须是字符串映射")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(item, str):
            raise ConfigError(f"{location} 必须是字符串映射")
        result[key] = _expand(item, f"{location}.{key}", environ)
    return result


def _expand(value: str, location: str, environ: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable not in environ:
            raise ConfigError(f"{location} 引用了未定义环境变量：{variable}")
        return environ[variable]

    expanded = _VARIABLE_PATTERN.sub(replace, value)
    if "${" in expanded:
        raise ConfigError(f"{location} 的环境变量引用格式无效")
    return expanded
