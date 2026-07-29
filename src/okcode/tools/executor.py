"""统一处理工具参数、超时和结构化结果。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass

from jsonschema import Draft202012Validator, ValidationError

from okcode.models import ToolCall
from okcode.permissions.manager import PermissionManager
from okcode.tools.base import Tool
from okcode.tools.models import (
    JSONValue,
    ToolErrorCode,
    ToolExecutionResult,
    ToolFailure,
    ToolOutput,
)
from okcode.tools.registry import ToolRegistry

_CONTENT_LIMIT = 12_000
_DATA_LIMIT = 16_000
_TRUNCATION_NOTICE = "\n[输出已截断]"


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    """已通过参数校验和权限预检、可以安全开始执行的工具调用。"""

    call: ToolCall
    tool: Tool
    arguments: Mapping[str, JSONValue]


class ToolExecutor:
    """工具执行的唯一入口，任何失败都转为结果而非异常。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permissions: PermissionManager | None = None,
        content_limit: int = _CONTENT_LIMIT,
        data_limit: int = _DATA_LIMIT,
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._content_limit = content_limit
        self._data_limit = data_limit

    async def execute(self, call: ToolCall) -> ToolExecutionResult:
        prepared = await self.prepare(call)
        if isinstance(prepared, ToolExecutionResult):
            return prepared
        return await self.execute_prepared(prepared)

    async def prepare(self, call: ToolCall) -> PreparedToolCall | ToolExecutionResult:
        """完成参数和权限检查，但不启动实际工具。"""

        tool = self._registry.get(call.name)
        if tool is None:
            return self._failure(
                call,
                ToolErrorCode.UNKNOWN_TOOL,
                f"不存在名为 {call.name!r} 的工具。",
            )
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            return self._failure(call, ToolErrorCode.INVALID_JSON, "工具参数不是合法 JSON 对象。")
        if not isinstance(arguments, dict):
            return self._failure(call, ToolErrorCode.INVALID_JSON, "工具参数必须是 JSON 对象。")

        try:
            Draft202012Validator(dict(tool.definition.input_schema)).validate(arguments)
        except ValidationError as error:
            location = error.json_path if error.json_path != "$" else "参数对象"
            return self._failure(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                f"工具参数无效：{location} {error.message}",
            )

        if self._permissions is not None:
            decision = await self._permissions.authorize_async(call, tool.definition, arguments)
            if not decision.allowed:
                return self._failure(
                    call,
                    decision.error_code,
                    decision.reason + " 调用未执行，请调整参数或改用其他方案。",
                    {
                        "permission_source": decision.source.value,
                        "permission_reason": decision.reason,
                        "executed": False,
                    },
                )
        return PreparedToolCall(call, tool, arguments)

    async def execute_prepared(self, prepared: PreparedToolCall) -> ToolExecutionResult:
        """执行已经通过预检的调用，并沿用原有失败与输出边界。"""

        call = prepared.call
        tool = prepared.tool
        arguments = prepared.arguments

        try:
            output = await asyncio.wait_for(
                tool.execute(arguments), timeout=tool.definition.timeout_seconds
            )
        except TimeoutError:
            return self._failure(
                call,
                ToolErrorCode.TIMEOUT,
                f"工具 {call.name} 执行超时。",
            )
        except ToolFailure as failure:
            return self._failure(call, failure.code, failure.content, failure.data)
        except Exception:
            return self._failure(
                call,
                ToolErrorCode.INTERNAL_ERROR,
                "工具执行出现内部错误，请调整参数后重试。",
            )

        content, data, truncated = self._bound_output(output)
        return ToolExecutionResult(
            tool_call_id=call.id,
            tool_name=call.name,
            success=True,
            content=content,
            error_code=None,
            data=data,
            truncated=truncated,
        )

    def _failure(
        self,
        call: ToolCall,
        code: ToolErrorCode,
        content: str,
        data: Mapping[str, JSONValue] | None = None,
    ) -> ToolExecutionResult:
        bounded_content, bounded_data, truncated = self._bound_values(content, data or {}, False)
        return ToolExecutionResult(
            tool_call_id=call.id,
            tool_name=call.name,
            success=False,
            content=bounded_content,
            error_code=code,
            data=bounded_data,
            truncated=truncated,
        )

    def _bound_output(self, output: ToolOutput) -> tuple[str, Mapping[str, JSONValue], bool]:
        return self._bound_values(output.content, output.data, output.truncated)

    def _bound_values(
        self,
        content: str,
        data: Mapping[str, JSONValue],
        truncated: bool,
    ) -> tuple[str, Mapping[str, JSONValue], bool]:
        if len(content) > self._content_limit:
            content = content[: self._content_limit] + _TRUNCATION_NOTICE
            truncated = True

        encoded_data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(encoded_data) > self._data_limit:
            data = {
                "truncated_data_preview": encoded_data[: self._data_limit],
                "data_truncated": True,
            }
            truncated = True
        return content, data, truncated
