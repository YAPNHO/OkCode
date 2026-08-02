"""Team Lead 和成员可见的团队工具。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from okcode.teams.models import (
    BackendPreference,
    TeamActorKind,
    TeamBackendKind,
    TeamMessage,
    TeamMessageProtocol,
    TeamToolContext,
)
from okcode.teams.runtime import TeamRuntime
from okcode.teams.serialization import to_jsonable
from okcode.tools.files import _object_schema
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.registry import ToolRegistry

TEAM_TASK_TOOL = "team_task"
TEAM_MESSAGE_TOOL = "team_message"
TEAM_MEMBER_TOOL = "team_member"
TEAM_MERGE_TOOL = "team_merge"
TEAM_TOOL_NAMES = (TEAM_TASK_TOOL, TEAM_MESSAGE_TOOL, TEAM_MEMBER_TOOL, TEAM_MERGE_TOOL)


class TeamToolSuite:
    """按团队上下文构造模型可见工具。"""

    def __init__(self, runtime: TeamRuntime, context: TeamToolContext | None) -> None:
        self._runtime = runtime
        self._context = context

    def register(self, registry: ToolRegistry) -> None:
        if self._context is None:
            return
        registry.register(TeamTaskTool(self._runtime, self._context))
        registry.register(TeamMessageTool(self._runtime, self._context))
        if self._context.actor_kind is TeamActorKind.LEAD:
            registry.register(TeamMemberTool(self._runtime, self._context))
            registry.register(TeamMergeTool(self._runtime, self._context))


class _TeamToolBase:
    def __init__(self, runtime: TeamRuntime, context: TeamToolContext) -> None:
        self._runtime = runtime
        self._context = context

    def _team_name(self, arguments: Mapping[str, JSONValue]) -> str:
        value = arguments.get("team_name") or self._context.team_name
        return str(value)

    def _require_context(self) -> None:
        if self._context is None:
            raise ToolFailure(ToolErrorCode.PERMISSION_DENIED, "当前会话不在团队上下文中。")

    def _output(self, content: str, data: object) -> ToolOutput:
        jsonable = to_jsonable(data)
        if isinstance(jsonable, dict):
            return ToolOutput(content, data=jsonable)
        return ToolOutput(content, data={"result": jsonable})


class TeamTaskTool(_TeamToolBase):
    """共享任务工具。"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=TEAM_TASK_TOOL,
            description="创建、查询、更新或关闭当前团队共享任务。",
            input_schema=_object_schema(
                {
                    "action": {"type": "string", "enum": ["create", "list", "update", "close"]},
                    "team_name": {"type": "string"},
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "owner": {"type": "string"},
                    "status": {"type": "string"},
                    "blocked_reason": {"type": "string"},
                    "output_summary": {"type": "string"},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                },
                ["action"],
            ),
            timeout_seconds=10,
            safety=ToolSafety.SIDE_EFFECT,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        self._require_context()
        action = str(arguments["action"])
        team_name = self._team_name(arguments)
        if action == "create":
            task = self._runtime.create_task(
                team_name,
                title=str(arguments.get("title", "")),
                body=str(arguments.get("body", "")),
                owner=_optional_str(arguments.get("owner")),
                dependencies=tuple(str(item) for item in arguments.get("dependencies", []) or []),
            )
            return self._output("团队任务已创建。", task)
        if action == "list":
            return self._output("团队任务列表。", {"tasks": self._runtime.list_tasks(team_name)})
        task_id = str(arguments.get("task_id", ""))
        if not task_id:
            raise ToolFailure(ToolErrorCode.INVALID_ARGUMENTS, "update/close 必须提供 task_id。")
        if action == "close":
            task = self._runtime.update_task(team_name, task_id, status="done")
            return self._output("团队任务已关闭。", task)
        task = self._runtime.update_task(
            team_name,
            task_id,
            status=_optional_str(arguments.get("status")),
            blocked_reason=_optional_str(arguments.get("blocked_reason")),
            output_summary=_optional_str(arguments.get("output_summary")),
            owner=_optional_str(arguments.get("owner")),
        )
        return self._output("团队任务已更新。", task)


class TeamMessageTool(_TeamToolBase):
    """团队消息工具。"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=TEAM_MESSAGE_TOOL,
            description="发送点对点团队消息、广播消息、读取未读消息或标记已读。",
            input_schema=_object_schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["send", "broadcast", "unread", "mark_read"],
                    },
                    "team_name": {"type": "string"},
                    "recipient": {"type": "string"},
                    "body": {"type": "string"},
                    "protocol": {"type": "string"},
                    "task_id": {"type": "string"},
                    "message_ids": {"type": "array", "items": {"type": "string"}},
                    "include_sender": {"type": "boolean"},
                },
                ["action"],
            ),
            timeout_seconds=10,
            safety=ToolSafety.SIDE_EFFECT,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        self._require_context()
        action = str(arguments["action"])
        team_name = self._team_name(arguments)
        actor = self._context.actor_name
        if action == "send":
            recipient = str(arguments.get("recipient", ""))
            if not recipient:
                raise ToolFailure(ToolErrorCode.INVALID_ARGUMENTS, "send 必须提供 recipient。")
            report = self._runtime.send_message(
                team_name,
                actor,
                recipient,
                _message(actor, recipient, arguments),
            )
            return self._output("团队消息发送完成。", report)
        if action == "broadcast":
            report = self._runtime.broadcast(
                team_name,
                actor,
                _message(actor, "", arguments),
                include_sender=bool(arguments.get("include_sender", False)),
            )
            return self._output("团队广播完成。", report)
        entry = self._runtime.store.read_registry(team_name).get(actor)
        if entry is None:
            raise ToolFailure(ToolErrorCode.NOT_FOUND, f"当前成员未注册邮箱：{actor}")
        if action == "unread":
            return self._output(
                "未读团队消息。",
                {"messages": self._runtime.mailbox.unread(entry.mailbox_path)},
            )
        ids = tuple(str(item) for item in arguments.get("message_ids", []) or [])
        messages = self._runtime.mailbox.mark_read(entry.mailbox_path, ids)
        return self._output("团队消息已标记已读。", {"messages": messages})


class TeamMemberTool(_TeamToolBase):
    """团队成员管理工具。"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=TEAM_MEMBER_TOOL,
            description="创建、唤醒、恢复、终止或查询团队成员。",
            input_schema=_object_schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["create", "wake", "restore", "terminate", "status"],
                    },
                    "team_name": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "workdir": {"type": "string"},
                    "approval_required": {"type": "boolean"},
                    "backend": {"type": "string", "enum": ["terminal_pane", "coroutine", "auto"]},
                    "require_strong_isolation": {"type": "boolean"},
                },
                ["action"],
            ),
            timeout_seconds=600,
            safety=ToolSafety.SIDE_EFFECT,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        self._require_context()
        action = str(arguments["action"])
        team_name = self._team_name(arguments)
        if action == "status":
            return self._output("团队成员状态。", self._runtime.snapshot(team_name))
        name = str(arguments.get("name", ""))
        if not name:
            raise ToolFailure(ToolErrorCode.INVALID_ARGUMENTS, "成员操作必须提供 name。")
        if action == "create":
            backend = str(arguments.get("backend", "auto"))
            required = None if backend == "auto" else TeamBackendKind(backend)
            member = self._runtime.add_member(
                team_name,
                name=name,
                role=str(arguments.get("role", "general-purpose")),
                workdir=Path(str(arguments.get("workdir", "."))),
                approval_required=bool(arguments.get("approval_required", False)),
                backend_preference=BackendPreference(
                    required_kind=required,
                    require_strong_isolation=bool(arguments.get("require_strong_isolation", False)),
                    allow_auto=backend == "auto",
                ),
            )
            return self._output("团队成员已创建。", member)
        if action == "wake":
            report = await self._runtime.wake_member_async(team_name, name)
            return self._output("团队成员唤醒并等待执行完成。", report)
        if action == "restore":
            return self._output("团队成员恢复完成。", self._runtime.restore_member(team_name, name))
        return self._output("团队成员终止完成。", self._runtime.terminate_member(team_name, name))


class TeamMergeTool(_TeamToolBase):
    """团队代码合并工具。"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=TEAM_MERGE_TOOL,
            description="检查或合并团队成员工作成果。",
            input_schema=_object_schema(
                {
                    "action": {"type": "string", "enum": ["inspect", "merge"]},
                    "team_name": {"type": "string"},
                    "members": {"type": "array", "items": {"type": "string"}},
                    "target_workspace": {"type": "string"},
                },
                ["action"],
            ),
            timeout_seconds=60,
            safety=ToolSafety.SIDE_EFFECT,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        from okcode.teams.models import TeamMergeReport, TeamMergeRequest, TeamMergeStatus

        self._require_context()
        team_name = self._team_name(arguments)
        members = tuple(str(item) for item in arguments.get("members", []) or [])
        target = Path(str(arguments.get("target_workspace", "."))).resolve()
        if str(arguments["action"]) == "inspect":
            return self._output(
                "团队合并检查完成。",
                {
                    "team_name": team_name,
                    "members": members,
                    "target_workspace": str(target),
                    "ready": bool(members),
                },
            )
        if not members:
            raise ToolFailure(ToolErrorCode.INVALID_ARGUMENTS, "merge 必须提供 members。")
        report = self._runtime.merge(team_name, TeamMergeRequest(team_name, members, target))
        if isinstance(report, TeamMergeReport):
            return self._output("团队合并完成。", report)
        return self._output(
            "团队合并失败。",
            TeamMergeReport(TeamMergeStatus.FAILED, message="未知合并结果。"),
        )


def register_team_tools(
    registry: ToolRegistry,
    runtime: TeamRuntime | None,
    context: TeamToolContext | None,
) -> None:
    """按团队上下文注册团队工具。"""

    if runtime is None or context is None:
        return
    TeamToolSuite(runtime, context).register(registry)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _message(sender: str, recipient: str, arguments: Mapping[str, JSONValue]) -> TeamMessage:
    protocol = TeamMessageProtocol(str(arguments.get("protocol", TeamMessageProtocol.TEXT.value)))
    return TeamMessage(
        sender=sender,
        recipient=recipient,
        body=str(arguments.get("body", "")),
        protocol=protocol,
        task_id=_optional_str(arguments.get("task_id")),
    )
