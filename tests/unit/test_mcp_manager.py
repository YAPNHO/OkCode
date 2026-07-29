from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import cast

import pytest
from mcp import ClientSession, types

from okcode.mcp.manager import McpClientManager, _Connection, _DiscoveryFailure
from okcode.mcp.models import McpCallError, StdioMcpServerConfig


def _tool(name: str = "echo") -> types.Tool:
    return types.Tool(
        name=name,
        description="返回输入。",
        inputSchema={"type": "object", "additionalProperties": False},
    )


def _result(text: str = "ok") -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=text)])


class FakeSession:
    def __init__(
        self,
        *,
        pages: list[types.ListToolsResult] | None = None,
        result: types.CallToolResult | Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.pages = list(pages or [])
        self.result = result or _result()
        self.delay = delay
        self.list_params: list[object] = []
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def list_tools(self, *, params: object = None) -> types.ListToolsResult:
        self.list_params.append(params)
        return self.pages.pop(0)

    async def call_tool(self, *_: object, **__: object) -> types.CallToolResult:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        finally:
            self.active -= 1


class FakeManager(McpClientManager):
    def __init__(
        self,
        servers: list[StdioMcpServerConfig],
        tools: dict[str, tuple[types.Tool, ...]],
    ) -> None:
        super().__init__(servers)
        self._tools = tools
        self.opened: list[str] = []
        self.closed: list[str] = []

    async def _open_connection(self, config: StdioMcpServerConfig) -> _Connection:  # type: ignore[override]
        if config.command == "fail":
            raise RuntimeError("not shown to user")
        self.opened.append(config.name)
        stack = AsyncExitStack()
        stack.push_async_callback(self._record_close, config.name)
        return _Connection(config, cast(ClientSession, FakeSession()), stack)

    async def _list_tools(self, connection: _Connection) -> tuple[types.Tool, ...]:
        return self._tools[connection.config.name]

    async def _record_close(self, name: str) -> None:
        self.closed.append(name)


async def test_discovery_isolates_failed_server_and_sorts_tools() -> None:
    servers = [
        StdioMcpServerConfig("bad", "fail"),
        StdioMcpServerConfig("good", "run"),
    ]
    manager = FakeManager(servers, {"good": (_tool("zeta"), _tool("alpha"))})

    result = await manager.discover_tools()

    assert [tool.definition.name for tool in result.tools] == [
        "mcp__good__alpha",
        "mcp__good__zeta",
    ]
    assert [(warning.server_name, warning.phase) for warning in result.warnings] == [
        ("bad", "发现")
    ]
    await manager.aclose()
    assert manager.closed == ["good"]


async def test_discovery_timeout_becomes_server_warning() -> None:
    class SlowManager(McpClientManager):
        async def _discover_one(self, _: StdioMcpServerConfig) -> object:  # type: ignore[override]
            await asyncio.sleep(0.05)
            raise AssertionError("发现超时后不应到达")

    manager = SlowManager(
        [StdioMcpServerConfig("slow", "run")],
        discovery_timeout_seconds=0.01,
    )

    result = await manager.discover_tools()

    assert result.tools == ()
    assert [(warning.server_name, warning.phase) for warning in result.warnings] == [
        ("slow", "发现")
    ]


async def test_discovery_skips_duplicate_and_provider_incompatible_tool_names() -> None:
    manager = FakeManager(
        [StdioMcpServerConfig("good", "run")],
        {"good": (_tool("echo"), _tool("echo"), _tool("x" * 60))},
    )

    result = await manager.discover_tools()

    assert [tool.definition.name for tool in result.tools] == ["mcp__good__echo"]
    assert [warning.phase for warning in result.warnings] == ["工具注册", "工具注册"]
    await manager.aclose()


async def test_paged_list_tools_detects_repeated_cursor() -> None:
    session = FakeSession(
        pages=[
            types.ListToolsResult(tools=[_tool("first")], nextCursor="next"),
            types.ListToolsResult(tools=[_tool("second")], nextCursor="next"),
        ]
    )
    manager = McpClientManager(())
    connection = _Connection(
        StdioMcpServerConfig("server", "run"),
        cast(ClientSession, session),
        AsyncExitStack(),
    )

    with pytest.raises(_DiscoveryFailure):
        await manager._list_tools(connection)
    assert len(session.list_params) == 2


async def test_same_server_calls_are_serialized_and_connection_failure_sticks() -> None:
    session = FakeSession(delay=0.02)
    connection = _Connection(
        StdioMcpServerConfig("server", "run"),
        cast(ClientSession, session),
        AsyncExitStack(),
    )
    manager = McpClientManager(())
    manager._connections["server"] = connection

    await asyncio.gather(
        manager.call_tool("server", "echo", {}),
        manager.call_tool("server", "echo", {}),
    )
    assert session.max_active == 1

    session.result = RuntimeError("connection dropped")
    with pytest.raises(McpCallError):
        await manager.call_tool("server", "echo", {})
    with pytest.raises(McpCallError):
        await manager.call_tool("server", "echo", {})
    assert session.calls == 3


async def test_aclose_continues_after_one_resource_close_error() -> None:
    closed: list[str] = []

    async def fail_close() -> None:
        raise RuntimeError("close failed")

    async def record_close() -> None:
        closed.append("second")

    first_stack = AsyncExitStack()
    first_stack.push_async_callback(fail_close)
    second_stack = AsyncExitStack()
    second_stack.push_async_callback(record_close)
    manager = McpClientManager(())
    manager._connections["first"] = _Connection(
        StdioMcpServerConfig("first", "run"),
        cast(ClientSession, FakeSession()),
        first_stack,
    )
    manager._connections["second"] = _Connection(
        StdioMcpServerConfig("second", "run"),
        cast(ClientSession, FakeSession()),
        second_stack,
    )

    await manager.aclose()
    assert closed == ["second"]
