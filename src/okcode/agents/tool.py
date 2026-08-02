"""模型可调用的统一 agent 工具。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from okcode.agents.launcher import AgentLauncher
from okcode.agents.models import (
    AgentIsolationMode,
    AgentLaunchKind,
    AgentTaskResult,
    AgentTaskSnapshot,
    AgentToolRequest,
    ParentAgentContext,
)
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)

AGENT_TOOL_NAME = "agent"


class AgentTool:
    """把子任务委派给定义式或 Fork 式子 Agent。"""

    def __init__(
        self,
        launcher: AgentLauncher,
        parent_context_provider: Callable[[], ParentAgentContext],
    ) -> None:
        self._launcher = launcher
        self._parent_context_provider = parent_context_provider

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=AGENT_TOOL_NAME,
            description=(
                "委派独立子任务给子 Agent。"
                "kind=defined 使用预定义角色；kind=fork 继承父对话快照并强制后台执行。"
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["defined", "fork"]},
                    "task": {"type": "string", "minLength": 1},
                    "role": {"type": "string"},
                    "background": {"type": "boolean"},
                    "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                    "max_turns": {"type": "integer", "minimum": 1},
                    "isolation": {"type": "string", "enum": ["shared", "worktree"]},
                    "worktree_name": {"type": "string"},
                },
                "required": ["kind", "task"],
            },
            timeout_seconds=600,
            safety=ToolSafety.READ_ONLY,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        request = _parse_request(arguments)
        try:
            outcome = await self._launcher.launch_from_tool(
                request,
                self._parent_context_provider(),
            )
        except Exception as exc:
            raise ToolFailure(
                code=ToolErrorCode.INTERNAL_ERROR,
                content=f"子 Agent 启动失败：{exc}",
            ) from exc
        return ToolOutput(_format_outcome(outcome), data=_data(outcome))


def _parse_request(arguments: Mapping[str, JSONValue]) -> AgentToolRequest:
    kind_raw = arguments.get("kind")
    task = arguments.get("task")
    if not isinstance(kind_raw, str):
        raise ToolFailure(
            code=ToolErrorCode.INVALID_ARGUMENTS, content="kind 必须是 defined 或 fork。"
        )
    try:
        kind = AgentLaunchKind(kind_raw)
    except ValueError as exc:
        raise ToolFailure(
            code=ToolErrorCode.INVALID_ARGUMENTS, content="kind 必须是 defined 或 fork。"
        ) from exc
    if not isinstance(task, str) or not task.strip():
        raise ToolFailure(code=ToolErrorCode.INVALID_ARGUMENTS, content="task 必须是非空字符串。")
    role = arguments.get("role")
    if role is not None and not isinstance(role, str):
        raise ToolFailure(code=ToolErrorCode.INVALID_ARGUMENTS, content="role 必须是字符串。")
    if kind is AgentLaunchKind.DEFINED and not role:
        raise ToolFailure(
            code=ToolErrorCode.INVALID_ARGUMENTS, content="defined 子 Agent 必须指定 role。"
        )
    background = arguments.get("background", False)
    if background is not None and not isinstance(background, bool):
        raise ToolFailure(code=ToolErrorCode.INVALID_ARGUMENTS, content="background 必须是布尔值。")
    timeout = arguments.get("timeout_seconds")
    if timeout is not None and not isinstance(timeout, int | float):
        raise ToolFailure(
            code=ToolErrorCode.INVALID_ARGUMENTS, content="timeout_seconds 必须是数字。"
        )
    max_turns = arguments.get("max_turns")
    if max_turns is not None and (not isinstance(max_turns, int) or isinstance(max_turns, bool)):
        raise ToolFailure(code=ToolErrorCode.INVALID_ARGUMENTS, content="max_turns 必须是正整数。")
    isolation_raw = arguments.get("isolation")
    isolation = None
    if isolation_raw is not None:
        if not isinstance(isolation_raw, str):
            raise ToolFailure(
                code=ToolErrorCode.INVALID_ARGUMENTS,
                content="isolation 必须是字符串。",
            )
        try:
            isolation = AgentIsolationMode(isolation_raw)
        except ValueError as exc:
            raise ToolFailure(
                code=ToolErrorCode.INVALID_ARGUMENTS,
                content="isolation 必须是 shared 或 worktree。",
            ) from exc
    worktree_name = arguments.get("worktree_name")
    if worktree_name is not None and not isinstance(worktree_name, str):
        raise ToolFailure(
            code=ToolErrorCode.INVALID_ARGUMENTS,
            content="worktree_name 必须是字符串。",
        )
    return AgentToolRequest(
        kind=kind,
        task=task.strip(),
        role=role.strip() if isinstance(role, str) else None,
        background=kind is AgentLaunchKind.FORK or bool(background),
        timeout_seconds=float(timeout) if timeout is not None else None,
        max_turns=max_turns,
        isolation=isolation,
        worktree_name=worktree_name.strip() if isinstance(worktree_name, str) else None,
    )


def _format_outcome(outcome: AgentTaskResult | AgentTaskSnapshot) -> str:
    return json.dumps(_data(outcome), ensure_ascii=False, sort_keys=True)


def _data(outcome: AgentTaskResult | AgentTaskSnapshot) -> dict[str, JSONValue]:
    if isinstance(outcome, AgentTaskResult):
        return {
            "task_id": outcome.task_id,
            "kind": outcome.kind.value,
            "status": outcome.status.value,
            "role": outcome.role_name,
            "summary": outcome.summary,
            "final_text": outcome.final_text,
            "error": outcome.error,
            "usage": {
                "input_tokens": outcome.usage.input_tokens,
                "output_tokens": outcome.usage.output_tokens,
                "model_requests": outcome.usage.model_request_count,
                "tool_calls": outcome.usage.tool_call_count,
            },
            "isolation": outcome.isolation.value,
            "worktree": _worktree_data(outcome.worktree),
        }
    return {
        "task_id": outcome.task_id,
        "kind": outcome.kind.value,
        "status": outcome.status.value,
        "role": outcome.role_name,
        "summary": outcome.summary or "子 Agent 已进入后台执行。使用 /tasks 查看状态。",
        "error": outcome.error,
        "isolation": outcome.isolation.value,
        "worktree": _worktree_data(outcome.worktree),
    }


def _worktree_data(report) -> dict[str, JSONValue] | None:
    if report is None:
        return None
    return {
        "path": str(report.path),
        "branch": report.branch,
        "cleanup": report.cleanup_decision.value,
        "message": report.cleanup_message,
        "protection_reasons": [reason.value for reason in report.protection_reasons],
        "changed_files": list(report.changed_files),
    }
