"""MCP 配置与运行期领域模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from okcode.tools.models import JSONValue

if TYPE_CHECKING:
    from okcode.mcp.tool import McpRemoteTool


class McpTransport(StrEnum):
    """本阶段支持的 MCP 传输方式。"""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


@dataclass(frozen=True, slots=True)
class StdioMcpServerConfig:
    """通过本地子进程通信的 MCP Server 配置。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamableHttpMcpServerConfig:
    """通过 Streamable HTTP 通信的 MCP Server 配置。"""

    name: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)


type McpServerConfig = StdioMcpServerConfig | StreamableHttpMcpServerConfig


@dataclass(frozen=True, slots=True)
class McpConfig:
    """已完成两层合并的 MCP 配置。"""

    servers: tuple[McpServerConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class McpConfigPaths:
    """用户级和项目级 MCP 配置的固定位置。"""

    user: Path
    project: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> McpConfigPaths:
        return cls(
            user=Path.home() / ".okcode" / "config.yaml",
            project=workspace_root / ".okcode" / "config.yaml",
        )


@dataclass(frozen=True, slots=True)
class McpRemoteToolInfo:
    """发现到的远端工具元数据，不暴露 SDK 对象。"""

    server_name: str
    remote_name: str
    description: str
    input_schema: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class McpDiscoveryWarning:
    """可显示给用户的脱敏 Server 级发现告警。"""

    server_name: str
    phase: str
    message: str


@dataclass(frozen=True, slots=True)
class McpDiscoveryResult:
    """本次启动中成功发现的工具和非致命告警。"""

    tools: tuple[McpRemoteTool, ...] = ()
    warnings: tuple[McpDiscoveryWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class McpCallResult:
    """远端工具调用的协议无关结果。"""

    text_parts: tuple[str, ...]
    data: Mapping[str, JSONValue] = field(default_factory=dict)
    is_error: bool = False
    unsupported_content_types: tuple[str, ...] = ()


class McpCallErrorKind(StrEnum):
    """适配层可转换为工具失败的运行期错误类别。"""

    UNAVAILABLE = "unavailable"
    UNSUPPORTED_RESULT = "unsupported_result"


class McpCallError(Exception):
    """MCP Manager 向适配层报告的安全调用失败。"""

    def __init__(self, kind: McpCallErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
