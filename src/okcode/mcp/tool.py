"""将 MCP 远端工具适配为 OkCode 统一工具接口。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from okcode.mcp.models import (
    McpCallError,
    McpCallErrorKind,
    McpCallResult,
    McpRemoteToolInfo,
)
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)

_REMOTE_TOOL_TIMEOUT_SECONDS = 30


class McpToolCaller(Protocol):
    """供适配层调用的最小连接管理器接口。"""

    async def call_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        arguments: Mapping[str, JSONValue],
    ) -> McpCallResult:
        """调用某个已发现的远端工具。"""


def visible_tool_name(server_name: str, remote_tool_name: str) -> str:
    """生成模型可见的唯一远端工具名称。"""

    return f"mcp__{server_name}__{remote_tool_name}"


def is_valid_visible_tool_name(name: str) -> bool:
    """检查 OpenAI 与 Anthropic 共用的函数名字符和长度限制。"""

    return (
        bool(name)
        and len(name) <= 64
        and all(
            character.isascii() and (character.isalnum() or character in "_-") for character in name
        )
    )


class McpRemoteTool:
    """通过 ``McpToolCaller`` 调用远端 MCP 工具。"""

    def __init__(self, info: McpRemoteToolInfo, caller: McpToolCaller) -> None:
        self._info = info
        self._caller = caller
        name = visible_tool_name(info.server_name, info.remote_name)
        if not is_valid_visible_tool_name(name):
            raise ValueError(f"MCP 工具名称不符合 Provider 限制：{name}")
        self._definition = ToolDefinition(
            name=name,
            description=info.description or f"来自 MCP Server {info.server_name} 的工具。",
            input_schema=info.input_schema,
            timeout_seconds=_REMOTE_TOOL_TIMEOUT_SECONDS,
            safety=ToolSafety.SIDE_EFFECT,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @property
    def server_name(self) -> str:
        """返回该工具所属的 MCP Server 名称，供启动告警使用。"""

        return self._info.server_name

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        try:
            result = await self._caller.call_tool(
                self._info.server_name,
                self._info.remote_name,
                arguments,
            )
        except McpCallError as error:
            code = (
                ToolErrorCode.MCP_UNAVAILABLE
                if error.kind is McpCallErrorKind.UNAVAILABLE
                else ToolErrorCode.MCP_UNSUPPORTED_RESULT
            )
            raise ToolFailure(code, error.message) from error

        text = "\n".join(part for part in result.text_parts if part)
        data = dict(result.data)
        if not result.is_error and not text and not data and result.unsupported_content_types:
            raise ToolFailure(
                ToolErrorCode.MCP_UNSUPPORTED_RESULT,
                "MCP 工具返回了当前不支持的非文本结果。",
            )
        if result.unsupported_content_types:
            data["unsupported_content_types"] = list(result.unsupported_content_types)
        if result.is_error:
            raise ToolFailure(
                ToolErrorCode.MCP_TOOL_ERROR,
                text or "MCP 工具返回了错误结果。",
                data,
            )
        if not text and not data:
            raise ToolFailure(
                ToolErrorCode.MCP_UNSUPPORTED_RESULT,
                "MCP 工具返回了当前不支持的非文本结果。",
            )
        return ToolOutput(text or "MCP 工具返回了结构化结果。", data)
