"""工具系统的通用数据结构。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]


class ToolErrorCode(StrEnum):
    """模型可据此调整调用方式的工具失败类别。"""

    UNKNOWN_TOOL = "unknown_tool"
    INVALID_JSON = "invalid_json"
    INVALID_ARGUMENTS = "invalid_arguments"
    OUTSIDE_WORKSPACE = "outside_workspace"
    NOT_FOUND = "not_found"
    IO_ERROR = "io_error"
    MATCH_NOT_FOUND = "match_not_found"
    MATCH_NOT_UNIQUE = "match_not_unique"
    COMMAND_FAILED = "command_failed"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    MCP_TOOL_ERROR = "mcp_tool_error"
    MCP_UNAVAILABLE = "mcp_unavailable"
    MCP_UNSUPPORTED_RESULT = "mcp_unsupported_result"
    INTERNAL_ERROR = "internal_error"


class ToolSafety(StrEnum):
    """工具调度时使用的安全类别。"""

    READ_ONLY = "read_only"
    SIDE_EFFECT = "side_effect"


class PermissionTargetKind(StrEnum):
    """权限规则匹配的工具主操作目标类别。"""

    NONE = "none"
    COMMAND = "command"
    PATH = "path"


@dataclass(frozen=True, slots=True)
class PermissionTarget:
    """工具主操作参数的权限匹配说明。"""

    kind: PermissionTargetKind = PermissionTargetKind.NONE
    argument_name: str | None = None
    optional: bool = False


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """供模型声明和运行时校验共用的工具元信息。"""

    name: str
    description: str
    input_schema: Mapping[str, JSONValue]
    timeout_seconds: float
    safety: ToolSafety = ToolSafety.SIDE_EFFECT
    permission_target: PermissionTarget = field(default_factory=PermissionTarget)


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """工具成功执行后的未封装输出。"""

    content: str
    data: Mapping[str, JSONValue] = field(default_factory=dict)
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """可安全写入会话历史的工具执行结果。"""

    tool_call_id: str
    tool_name: str
    success: bool
    content: str
    error_code: ToolErrorCode | None
    data: Mapping[str, JSONValue] = field(default_factory=dict)
    truncated: bool = False

    def to_json(self) -> str:
        """生成协议无关、稳定的工具结果文本。"""

        return json.dumps(
            {
                "success": self.success,
                "tool": self.tool_name,
                "content": self.content,
                "error_code": self.error_code.value if self.error_code else None,
                "data": self.data,
                "truncated": self.truncated,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class ToolFailure(Exception):
    """工具可预期失败，交由执行器转换为结构化结果。"""

    def __init__(
        self,
        code: ToolErrorCode,
        content: str,
        data: Mapping[str, JSONValue] | None = None,
    ) -> None:
        super().__init__(content)
        self.code = code
        self.content = content
        self.data = data or {}
