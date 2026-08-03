"""子 Agent 按工作区构造运行时依赖。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from okcode.agents.filtering import FilteredToolRegistry
from okcode.agents.models import AgentIsolationMode, AgentLaunchKind, AgentLaunchRequest
from okcode.context import ArtifactStore, ContextManager
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import PermissionConfirmation, PermissionMode
from okcode.prompt import (
    PromptBuildContext,
    PromptOptionalSections,
    SystemInstruction,
    TurnKind,
)
from okcode.tools.defaults import build_child_registry
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolDefinition
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace


@dataclass(frozen=True, slots=True)
class ChildAgentRuntime:
    """一个子 Agent 在指定工作区内使用的一组运行时对象。"""

    workspace_root: Path
    workspace: Workspace
    registry: FilteredToolRegistry
    executor: ToolExecutor
    permissions: PermissionManager | None
    context_manager: ContextManager
    context_factory: Callable[[TurnKind, int, Sequence[ToolDefinition]], PromptBuildContext]


def build_child_runtime(
    request: AgentLaunchRequest,
    base_registry: ToolRegistry,
    *,
    workspace_root: Path,
    parent_permissions: PermissionManager | None = None,
    worktree_note: str | None = None,
) -> ChildAgentRuntime:
    """为子 Agent 的显式 workspace_root 构造路径绑定依赖。"""

    workspace = Workspace(workspace_root)
    permissions = _clone_permissions(parent_permissions, request.permission_mode, workspace)
    local_registry = (
        build_child_registry(base_registry, workspace)
        if request.isolation is AgentIsolationMode.WORKTREE
        else base_registry
    )
    registry = FilteredToolRegistry(local_registry, request.visible_tool_names)
    return ChildAgentRuntime(
        workspace_root=workspace_root,
        workspace=workspace,
        registry=registry,
        executor=ToolExecutor(registry, permissions=permissions),
        permissions=permissions,
        context_manager=ContextManager(ArtifactStore(workspace_root)),
        context_factory=build_child_context_factory(request, workspace_root, worktree_note),
    )


def build_child_context_factory(
    request: AgentLaunchRequest,
    workspace_root: Path,
    worktree_note: str | None = None,
) -> Callable[[TurnKind, int, Sequence[ToolDefinition]], PromptBuildContext]:
    """构造子 Agent 的动态系统提示上下文。"""

    def build(
        turn_kind: TurnKind,
        iteration: int,
        tools: Sequence[ToolDefinition],
    ) -> PromptBuildContext:
        instructions: list[SystemInstruction] = []
        if request.role is not None:
            instructions.append(
                SystemInstruction("subagent_role", request.role.system_prompt, priority=70)
            )
        if request.kind is AgentLaunchKind.FORK:
            instructions.append(
                SystemInstruction(
                    "subagent_fork",
                    "你正在作为 Fork 式子 Agent 执行子任务。请基于已有父对话快照工作，"
                    "不要假设可以向用户直接追问；需要用户决策时返回阻塞说明。",
                    priority=75,
                )
            )
        if worktree_note:
            instructions.append(SystemInstruction("subagent_worktree", worktree_note, priority=76))
        return PromptBuildContext(
            workspace_root=str(workspace_root),
            platform="testable",
            current_date="2026-08-01",
            available_tool_names=tuple(tool.name for tool in tools),
            turn_kind=turn_kind,
            iteration=iteration,
            optional_sections=PromptOptionalSections(),
            additional_system_instructions=tuple(instructions),
        )

    return build


def _clone_permissions(
    parent: PermissionManager | None,
    mode: PermissionMode,
    workspace: Workspace | None = None,
) -> PermissionManager | None:
    if parent is None:
        return None
    return PermissionManager(
        workspace or getattr(parent, "_workspace"),
        tuple(getattr(parent, "_rule_sets")),
        parent.paths,
        set(getattr(parent, "_known_tool_names")),
        mode=mode,
        confirmer=lambda _: PermissionConfirmation.DENY,
    )
