from __future__ import annotations

import sys
from pathlib import Path

from okcode.mcp.manager import McpClientManager
from okcode.mcp.models import StdioMcpServerConfig


async def test_stdio_server_is_discovered_and_called() -> None:
    helper = Path(__file__).parents[1] / "helpers" / "mcp_stdio_server.py"
    manager = McpClientManager([StdioMcpServerConfig("stdio", sys.executable, args=(str(helper),))])
    try:
        discovery = await manager.discover_tools()
        assert discovery.warnings == ()
        assert [tool.definition.name for tool in discovery.tools] == ["mcp__stdio__echo"]

        result = await discovery.tools[0].execute({"text": "hello"})
        assert result.content == "echo:hello"
    finally:
        await manager.aclose()
