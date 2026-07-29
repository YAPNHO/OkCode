"""供 stdio 集成测试启动的最小 MCP Server。"""

from __future__ import annotations

import asyncio

from mcp import types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server


async def _list_tools(_: object, __: object) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="echo",
                description="返回输入文本。",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            )
        ]
    )


async def _call_tool(_: object, params: types.CallToolRequestParams) -> types.CallToolResult:
    text = str((params.arguments or {}).get("text", ""))
    return types.CallToolResult(content=[types.TextContent(text=f"echo:{text}")])


async def main() -> None:
    server = Server("stdio-test", on_list_tools=_list_tools, on_call_tool=_call_tool)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
