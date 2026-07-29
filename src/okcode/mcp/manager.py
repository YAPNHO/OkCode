"""MCP Server 连接、工具发现、调用和生命周期管理。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, cast

import httpx2
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from okcode.mcp.config import stdio_environment
from okcode.mcp.models import (
    McpCallError,
    McpCallErrorKind,
    McpCallResult,
    McpDiscoveryResult,
    McpDiscoveryWarning,
    McpRemoteToolInfo,
    McpServerConfig,
    StdioMcpServerConfig,
    StreamableHttpMcpServerConfig,
)
from okcode.mcp.tool import McpRemoteTool, is_valid_visible_tool_name, visible_tool_name
from okcode.tools.models import JSONValue

_DISCOVERY_TIMEOUT_SECONDS = 10
_CALL_TIMEOUT_SECONDS = 30


@dataclass(slots=True)
class _Connection:
    """一个已初始化 Server 的会话和资源所有权。"""

    config: McpServerConfig
    session: ClientSession
    stack: AsyncExitStack
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    available: bool = True


@dataclass(frozen=True, slots=True)
class _DiscoveryOutcome:
    """单个 Server 的成功结果或可显示告警。"""

    connection: _Connection | None
    tools: tuple[McpRemoteTool, ...] = ()
    warnings: tuple[McpDiscoveryWarning, ...] = ()


class _DiscoveryFailure(Exception):
    """为发现异常附加一个安全阶段名称。"""

    def __init__(self, phase: str) -> None:
        super().__init__(phase)
        self.phase = phase


class McpClientManager:
    """管理多个独立 MCP Server 会话。"""

    def __init__(
        self,
        servers: Sequence[McpServerConfig],
        *,
        discovery_timeout_seconds: float = _DISCOVERY_TIMEOUT_SECONDS,
    ) -> None:
        self._servers = tuple(servers)
        self._discovery_timeout_seconds = discovery_timeout_seconds
        self._connections: dict[str, _Connection] = {}
        self._discovery_result: McpDiscoveryResult | None = None

    async def discover_tools(self) -> McpDiscoveryResult:
        """并行连接 Server，返回可注册远端工具和非致命告警。"""

        if self._discovery_result is not None:
            return self._discovery_result
        outcomes = await asyncio.gather(
            *(self._discover_safely(config) for config in self._servers)
        )
        tools: list[McpRemoteTool] = []
        warnings: list[McpDiscoveryWarning] = []
        for outcome in outcomes:
            warnings.extend(outcome.warnings)
            if outcome.connection is not None:
                self._connections[outcome.connection.config.name] = outcome.connection
            tools.extend(outcome.tools)
        self._discovery_result = McpDiscoveryResult(
            tools=tuple(sorted(tools, key=lambda tool: tool.definition.name)),
            warnings=tuple(
                sorted(warnings, key=lambda warning: (warning.server_name, warning.phase))
            ),
        )
        return self._discovery_result

    async def call_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        arguments: Mapping[str, JSONValue],
    ) -> McpCallResult:
        """调用已发现的工具；连接断开时不尝试自动重连。"""

        connection = self._connections.get(server_name)
        if connection is None or not connection.available:
            raise McpCallError(McpCallErrorKind.UNAVAILABLE, "MCP Server 当前不可用。")
        async with connection.lock:
            if not connection.available:
                raise McpCallError(McpCallErrorKind.UNAVAILABLE, "MCP Server 当前不可用。")
            try:
                result = await connection.session.call_tool(
                    remote_tool_name,
                    arguments=dict(arguments),
                    read_timeout_seconds=_CALL_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                await self._mark_unavailable(connection)
                raise
            except Exception as exc:
                await self._mark_unavailable(connection)
                raise McpCallError(McpCallErrorKind.UNAVAILABLE, "MCP Server 调用失败。") from exc
        if not isinstance(result, types.CallToolResult):
            raise McpCallError(
                McpCallErrorKind.UNSUPPORTED_RESULT,
                "MCP Server 请求了当前不支持的交互结果。",
            )
        return _call_result(result)

    async def aclose(self) -> None:
        """关闭全部 Server；单个关闭失败不能阻断其余清理。"""

        connections = tuple(self._connections.values())
        self._connections.clear()
        await asyncio.gather(
            *(self._close_connection(connection) for connection in connections),
            return_exceptions=True,
        )

    async def _discover_safely(self, config: McpServerConfig) -> _DiscoveryOutcome:
        try:
            return await asyncio.wait_for(
                self._discover_one(config),
                timeout=self._discovery_timeout_seconds,
            )
        except TimeoutError:
            return _warning_outcome(config.name, "发现", "MCP Server 连接或工具发现超时。")
        except _DiscoveryFailure as error:
            return _warning_outcome(
                config.name,
                error.phase,
                f"MCP Server 在{error.phase}阶段失败。",
            )
        except Exception:
            return _warning_outcome(config.name, "发现", "MCP Server 工具发现失败。")

    async def _discover_one(self, config: McpServerConfig) -> _DiscoveryOutcome:
        connection = await self._open_connection(config)
        try:
            remote_tools = await self._list_tools(connection)
            tools, warnings = _adapt_tools(config.name, remote_tools, self)
            return _DiscoveryOutcome(connection, tools, warnings)
        except BaseException:
            await self._close_connection(connection)
            raise

    async def _open_connection(self, config: McpServerConfig) -> _Connection:
        stack = AsyncExitStack()
        try:
            if isinstance(config, StdioMcpServerConfig):
                server = StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    env=stdio_environment(config),
                )
                read_stream, write_stream = await stack.enter_async_context(stdio_client(server))
            else:
                assert isinstance(config, StreamableHttpMcpServerConfig)
                http_client = httpx2.AsyncClient(
                    headers=dict(config.headers),
                    timeout=httpx2.Timeout(_DISCOVERY_TIMEOUT_SECONDS, read=_CALL_TIMEOUT_SECONDS),
                    follow_redirects=True,
                )
                await stack.enter_async_context(http_client)
                read_stream, write_stream = await stack.enter_async_context(
                    streamable_http_client(config.url, http_client=http_client)
                )
            session = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=_CALL_TIMEOUT_SECONDS,
            )
            await stack.enter_async_context(session)
            try:
                await session.initialize()
            except Exception as exc:
                raise _DiscoveryFailure("初始化") from exc
            return _Connection(config, session, stack)
        except BaseException:
            await stack.aclose()
            raise

    async def _list_tools(self, connection: _Connection) -> tuple[types.Tool, ...]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        tools: list[types.Tool] = []
        while True:
            try:
                params = types.PaginatedRequestParams(cursor=cursor) if cursor is not None else None
                page = await connection.session.list_tools(params=params)
            except Exception as exc:
                raise _DiscoveryFailure("工具发现") from exc
            tools.extend(page.tools)
            next_cursor = page.next_cursor
            if next_cursor is None:
                return tuple(tools)
            if next_cursor in seen_cursors:
                raise _DiscoveryFailure("工具发现")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def _mark_unavailable(self, connection: _Connection) -> None:
        connection.available = False
        try:
            await connection.stack.aclose()
        except Exception:
            pass

    async def _close_connection(self, connection: _Connection) -> None:
        connection.available = False
        await connection.stack.aclose()


def _warning_outcome(server_name: str, phase: str, message: str) -> _DiscoveryOutcome:
    return _DiscoveryOutcome(None, warnings=(McpDiscoveryWarning(server_name, phase, message),))


def _adapt_tools(
    server_name: str,
    remote_tools: Sequence[types.Tool],
    caller: McpClientManager,
) -> tuple[tuple[McpRemoteTool, ...], tuple[McpDiscoveryWarning, ...]]:
    tools: list[McpRemoteTool] = []
    warnings: list[McpDiscoveryWarning] = []
    seen_remote_names: set[str] = set()
    for remote in remote_tools:
        if remote.name in seen_remote_names:
            warnings.append(
                McpDiscoveryWarning(
                    server_name,
                    "工具注册",
                    "MCP Server 返回了重复工具名，已跳过。",
                )
            )
            continue
        seen_remote_names.add(remote.name)
        visible_name = visible_tool_name(server_name, remote.name)
        if not is_valid_visible_tool_name(visible_name):
            warnings.append(
                McpDiscoveryWarning(
                    server_name,
                    "工具注册",
                    "MCP 工具名称不符合 Provider 限制，已跳过。",
                )
            )
            continue
        try:
            Draft202012Validator.check_schema(remote.input_schema)
        except SchemaError:
            warnings.append(
                McpDiscoveryWarning(server_name, "工具注册", "MCP 工具参数 Schema 无效，已跳过。")
            )
            continue
        info = McpRemoteToolInfo(
            server_name=server_name,
            remote_name=remote.name,
            description=remote.description or "",
            input_schema=cast(Mapping[str, JSONValue], remote.input_schema),
        )
        tools.append(McpRemoteTool(info, caller))
    return tuple(tools), tuple(warnings)


def _call_result(result: types.CallToolResult) -> McpCallResult:
    text_parts = tuple(
        content.text for content in result.content if isinstance(content, types.TextContent)
    )
    unsupported = tuple(
        str(getattr(content, "type", "unknown"))
        for content in result.content
        if not isinstance(content, types.TextContent)
    )
    data: dict[str, JSONValue] = {}
    if result.structured_content is not None:
        data["structured_content"] = _normalise_json(result.structured_content)
    return McpCallResult(
        text_parts=text_parts,
        data=data,
        is_error=result.is_error,
        unsupported_content_types=unsupported,
    )


def _normalise_json(value: Any) -> JSONValue:
    """将 SDK 返回的 JSON 兼容值转换为工具结果允许的递归类型。"""

    encoded = json.dumps(value, ensure_ascii=False, default=_json_default)
    return cast(JSONValue, json.loads(encoded))


def _json_default(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return str(value)
