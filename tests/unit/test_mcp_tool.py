from __future__ import annotations

from collections.abc import Mapping

import pytest

from okcode.mcp.models import (
    McpCallError,
    McpCallErrorKind,
    McpCallResult,
    McpRemoteToolInfo,
)
from okcode.mcp.tool import McpRemoteTool, visible_tool_name
from okcode.tools.models import JSONValue, ToolErrorCode, ToolFailure, ToolSafety


class FakeCaller:
    def __init__(self, result: McpCallResult | Exception) -> None:
        self._result = result
        self.calls: list[tuple[str, str, Mapping[str, JSONValue]]] = []

    async def call_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        arguments: Mapping[str, JSONValue],
    ) -> McpCallResult:
        self.calls.append((server_name, remote_tool_name, arguments))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _info(name: str = "echo") -> McpRemoteToolInfo:
    return McpRemoteToolInfo(
        "server",
        name,
        "返回输入文本。",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )


async def test_remote_tool_preserves_definition_and_routes_original_name() -> None:
    caller = FakeCaller(McpCallResult(("hello",), {"structured_content": {"ok": True}}))
    tool = McpRemoteTool(_info(), caller)

    assert tool.definition.name == "mcp__server__echo"
    assert tool.definition.safety is ToolSafety.SIDE_EFFECT
    assert tool.definition.input_schema == _info().input_schema

    result = await tool.execute({"text": "hello"})
    assert result.content == "hello"
    assert result.data == {"structured_content": {"ok": True}}
    assert caller.calls == [("server", "echo", {"text": "hello"})]


@pytest.mark.parametrize(
    "result, code",
    [
        (McpCallResult(("remote failed",), is_error=True), ToolErrorCode.MCP_TOOL_ERROR),
        (
            McpCallResult((), unsupported_content_types=("image",)),
            ToolErrorCode.MCP_UNSUPPORTED_RESULT,
        ),
    ],
)
async def test_remote_tool_converts_server_failures(
    result: McpCallResult,
    code: ToolErrorCode,
) -> None:
    tool = McpRemoteTool(_info(), FakeCaller(result))
    with pytest.raises(ToolFailure) as error:
        await tool.execute({"text": "hello"})
    assert error.value.code is code


async def test_remote_tool_converts_unavailable_connection() -> None:
    tool = McpRemoteTool(
        _info(),
        FakeCaller(McpCallError(McpCallErrorKind.UNAVAILABLE, "MCP Server 当前不可用。")),
    )
    with pytest.raises(ToolFailure) as error:
        await tool.execute({"text": "hello"})
    assert error.value.code is ToolErrorCode.MCP_UNAVAILABLE


def test_remote_tool_rejects_provider_incompatible_name() -> None:
    too_long = "x" * 60
    assert len(visible_tool_name("server", too_long)) > 64
    with pytest.raises(ValueError, match="Provider"):
        McpRemoteTool(_info(too_long), FakeCaller(McpCallResult(("ok",))))
