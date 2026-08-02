"""子 Agent 统一启动器。"""

from __future__ import annotations

import uuid

from okcode.agents.filtering import filter_agent_tools
from okcode.agents.manager import AgentTaskManager
from okcode.agents.models import (
    AgentExecutionMode,
    AgentIsolationMode,
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
from okcode.worktrees.models import WorktreeIdentity, WorktreePrepareRequest
from okcode.worktrees.naming import (
    derive_agent_branch_name,
    derive_agent_worktree_name,
    validate_worktree_name,
)


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
        isolation = _effective_isolation(request, role)
        background_allowed = _background_allowed_tools(
            self._registry,
            execution_mode,
            isolation,
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
        task_id = str(uuid.uuid4())
        worktree_request = None
        if isolation is AgentIsolationMode.WORKTREE:
            name = (
                validate_worktree_name(request.worktree_name)
                if request.worktree_name
                else derive_agent_worktree_name(role.name if role is not None else None, task_id)
            )
            branch = derive_agent_branch_name(name)
            worktree_request = WorktreePrepareRequest(
                identity=WorktreeIdentity(
                    name=name,
                    branch=branch,
                    task_id=task_id,
                    parent_session_id=parent.session_id,
                    role_name=role.name if role is not None else None,
                    trigger=trigger,
                ),
                main_workspace=parent.workspace_root,
            )
        return AgentLaunchRequest(
            task_id=task_id,
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
            isolation=isolation,
            worktree_request=worktree_request,
            main_workspace_root=parent.workspace_root,
        )


def _permission_mode(role: object, parent_mode: PermissionMode) -> PermissionMode:
    if role is None:
        return parent_mode
    policy = getattr(role, "permission_policy")
    return policy.resolved_mode or parent_mode


def _effective_isolation(request: AgentToolRequest, role: object) -> AgentIsolationMode:
    role_isolation = (
        getattr(role, "isolation", AgentIsolationMode.SHARED)
        if role is not None
        else AgentIsolationMode.SHARED
    )
    if role_isolation is AgentIsolationMode.WORKTREE:
        return AgentIsolationMode.WORKTREE
    return request.isolation or role_isolation


def _read_only_tool_names(registry: ToolRegistry) -> tuple[str, ...]:
    from okcode.tools.models import ToolSafety

    return tuple(
        definition.name for definition in registry.definitions_by_safety(ToolSafety.READ_ONLY)
    )


def _background_allowed_tools(
    registry: ToolRegistry,
    execution_mode: AgentExecutionMode,
    isolation: AgentIsolationMode,
) -> tuple[str, ...] | None:
    if execution_mode is not AgentExecutionMode.BACKGROUND:
        return None
    if isolation is AgentIsolationMode.WORKTREE:
        return None
    return _read_only_tool_names(registry)
