from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

from okcode.mcp.manager import McpClientManager
from okcode.mcp.models import StreamableHttpMcpServerConfig


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_for_port(port: int) -> None:
    for _ in range(100):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        await writer.wait_closed()
        del reader
        return
    raise AssertionError("HTTP MCP 测试 Server 未在预期时间内启动")


async def test_streamable_http_server_receives_configured_header() -> None:
    port = _available_port()
    authorization = "Bearer integration-token"
    helper = Path(__file__).parents[1] / "helpers" / "mcp_http_server.py"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(helper),
        str(port),
        authorization,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    manager = McpClientManager(
        [
            StreamableHttpMcpServerConfig(
                "http",
                f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": authorization},
            )
        ]
    )
    try:
        await _wait_for_port(port)
        discovery = await manager.discover_tools()
        assert discovery.warnings == ()
        assert [tool.definition.name for tool in discovery.tools] == ["mcp__http__echo"]

        result = await discovery.tools[0].execute({"text": "hello"})
        assert result.content == "echo:hello"
    finally:
        await manager.aclose()
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
