"""供 Streamable HTTP 集成测试启动的最小 MCP Server。"""

from __future__ import annotations

import sys

import uvicorn
from mcp import types
from mcp.server.lowlevel.server import Server


def _server(expected_authorization: str) -> Server:
    async def list_tools(_: object, __: object) -> types.ListToolsResult:
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

    async def call_tool(
        context: object,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        request = getattr(context, "request", None)
        headers = getattr(request, "headers", {})
        if headers.get("authorization") != expected_authorization:
            return types.CallToolResult(
                content=[types.TextContent(text="missing authorization")],
                isError=True,
            )
        text = str((params.arguments or {}).get("text", ""))
        return types.CallToolResult(content=[types.TextContent(text=f"echo:{text}")])

    return Server("http-test", on_list_tools=list_tools, on_call_tool=call_tool)


def main() -> None:
    port = int(sys.argv[1])
    expected_authorization = sys.argv[2]
    server = _server(expected_authorization)
    uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
