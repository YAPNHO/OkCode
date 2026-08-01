"""子 Agent 统一启动器。"""

from __future__ import annotations

import uuid

from okcode.agents.filtering import filter_agent_tools
from okcode.agents.manager import AgentTaskManager
from okcode.agents.models import (
    AgentExecutionMode,
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentTaskResult,
    AgentTaskSnapshot,
    AgentToolPolicy,
    AgentToolRequest,
    ParentAgentContext,
)
from okcode.agents.roles import AgentRoleCatalog
from okcode.hooks.models import HookContext, SubAgentHookAction
from okcode.permissions.models import PermissionMode
from okcode.tools.registry import ToolRegistry


class AgentLauncher:
    """供 AgentTool 和 HookActionRunner 共用的子 Agent 启动入口。"""

    def __init__(
        self,
        roles: AgentRoleCatalog,
        registry: ToolRegistry,
        manager: AgentTaskManager,
    ) -> None:
        self._roles = roles
        self._registry = registry
        self._manager = manager

    async def launch_from_tool(
        self,
        request: AgentToolRequest,
        parent: ParentAgentContext,
    ) -> AgentTaskResult | AgentTaskSnapshot:
        launch = self._build_launch_request(request, parent, trigger="tool")
        return await self._manager.run(launch)

    def launch_from_hook(
        self,
        action: SubAgentHookAction,
        context: HookContext,
        parent: ParentAgentContext,
    ) -> AgentTaskSnapshot:
        tool_request = AgentToolRequest(
            kind=AgentLaunchKind.DEFINED,
            task=action.task,
            role=action.profile,
            background=True,
        )
        launch = self._build_launch_request(
            tool_request, parent, trigger=f"hook:{context.event.value}"
        )
        return self._manager.start(launch)

    def _build_launch_request(
        self,
        request: AgentToolRequest,
        parent: ParentAgentContext,
        *,
        trigger: str,
    ) -> AgentLaunchRequest:
        role = None
        if request.kind is AgentLaunchKind.DEFINED:
            if request.role is None:
                raise LookupError("定义式子 Agent 必须指定角色。")
            role = self._roles.get(request.role)
        execution_mode = (
            AgentExecutionMode.BACKGROUND
            if request.kind is AgentLaunchKind.FORK or request.background
            else AgentExecutionMode.FOREGROUND
        )
        max_turns = request.max_turns or (role.max_turns if role is not None else 6)
        permission_mode = _permission_mode(role, parent.permission_mode)
        parent_tool_names = parent.visible_tool_names
        background_allowed = (
            _read_only_tool_names(self._registry)
            if execution_mode is AgentExecutionMode.BACKGROUND
            else None
        )
        filter_result = filter_agent_tools(
            self._registry,
            AgentToolPolicy(
                background_allowed=background_allowed,
                parent_allowed=parent_tool_names,
                role_allowlist=role.tool_allowlist if role is not None else (),
                role_denylist=role.tool_denylist if role is not None else (),
                depth=parent.depth,
            ),
        )
        return AgentLaunchRequest(
            task_id=str(uuid.uuid4()),
            kind=request.kind,
            task=request.task,
            parent_session_id=parent.session_id,
            role=role,
            parent_messages=parent.messages if request.kind is AgentLaunchKind.FORK else (),
            parent_tool_names=parent_tool_names,
            visible_tool_names=filter_result.registry.visible_tool_names,
            tool_denied_reasons=dict(filter_result.denied_reasons),
            execution_mode=execution_mode,
            timeout_seconds=request.timeout_seconds,
            max_turns=max_turns,
            depth=parent.depth + 1,
            trigger=trigger,
            runtime_mode=parent.runtime_mode,
            permission_mode=permission_mode,
        )


def _permission_mode(role: object, parent_mode: PermissionMode) -> PermissionMode:
    if role is None:
        return parent_mode
    policy = getattr(role, "permission_policy")
    return policy.resolved_mode or parent_mode


def _read_only_tool_names(registry: ToolRegistry) -> tuple[str, ...]:
    from okcode.tools.models import ToolSafety

    return tuple(
        definition.name for definition in registry.definitions_by_safety(ToolSafety.READ_ONLY)
    )
