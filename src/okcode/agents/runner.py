"""隔离子 Agent 运行器。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from okcode.agents.filtering import FilteredToolRegistry
from okcode.agents.manager import AgentCancelToken
from okcode.agents.models import (
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentTaskResult,
    AgentTaskStatus,
    AgentUsage,
)
from okcode.context import ArtifactStore, ContextManager
from okcode.conversation import AgentConfig, ConversationSession
from okcode.errors import ProviderError
from okcode.models import (
    AgentStopped,
    AgentStopReason,
    ChatMessage,
    Role,
    TokenUsageReported,
    ToolExecutionFinished,
)
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import PermissionConfirmation, PermissionMode
from okcode.prompt import (
    PromptBuildContext,
    PromptCachePolicy,
    PromptOptionalSections,
    SystemInstruction,
    TurnKind,
)
from okcode.providers.base import LLMProvider
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolDefinition
from okcode.tools.registry import ToolRegistry

ProviderFactory = Callable[[str | None], LLMProvider]


class AgentRunner:
    """运行一个子 Agent，直到自然完成、失败或停止。"""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        registry: ToolRegistry,
        *,
        workspace_root: Path,
        cache_policy: PromptCachePolicy | None = None,
        parent_permissions: PermissionManager | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._registry = registry
        self._workspace_root = workspace_root
        self._cache_policy = cache_policy or PromptCachePolicy()
        self._parent_permissions = parent_permissions

    async def run(
        self,
        request: AgentLaunchRequest,
        cancel_token: AgentCancelToken,
    ) -> AgentTaskResult:
        """执行子 Agent 并汇总可返回给父对话的结果。"""

        provider = self._provider_factory(_model_override(request))
        permissions = _clone_permissions(self._parent_permissions, request.permission_mode)
        registry = FilteredToolRegistry(self._registry, request.visible_tool_names)
        executor = ToolExecutor(registry, permissions=permissions)
        session = ConversationSession(
            provider,
            registry,
            executor,
            config=AgentConfig(max_iterations=request.max_turns),
            cache_policy=self._cache_policy,
            permissions=permissions,
            context_manager=ContextManager(ArtifactStore(self._workspace_root)),
            context_factory=_context_factory(request, self._workspace_root),
            workspace_root=self._workspace_root,
            model_name=_model_override(request) or "",
            initial_messages=request.parent_messages
            if request.kind is AgentLaunchKind.FORK
            else (),
        )

        usage = AgentUsage()
        stopped: AgentStopped | None = None
        try:
            async for event in session.stream_user_message(request.task):
                if cancel_token.cancelled:
                    return _cancelled_result(request, usage, session)
                if isinstance(event, TokenUsageReported):
                    usage = usage.add_token_usage(event.usage)
                elif isinstance(event, ToolExecutionFinished):
                    usage = usage.add_tool_calls(1)
                elif isinstance(event, AgentStopped):
                    stopped = event
        except ProviderError as exc:
            return _failed_result(request, exc.safe_message, usage, session)
        except Exception as exc:
            return _failed_result(request, str(exc), usage, session)

        final_text = _last_assistant_text(session.messages)
        if stopped is not None:
            status = (
                AgentTaskStatus.INCOMPLETE
                if stopped.reason
                in {AgentStopReason.ITERATION_LIMIT, AgentStopReason.UNKNOWN_TOOL_LIMIT}
                else AgentTaskStatus.FAILED
            )
            return AgentTaskResult(
                task_id=request.task_id,
                kind=request.kind,
                role_name=request.role.name if request.role else None,
                status=status,
                final_text=final_text,
                summary=stopped.message,
                error=stopped.message,
                rounds=usage.model_request_count,
                usage=usage,
            )
        return AgentTaskResult(
            task_id=request.task_id,
            kind=request.kind,
            role_name=request.role.name if request.role else None,
            status=AgentTaskStatus.COMPLETED,
            final_text=final_text,
            summary=_summary(final_text),
            rounds=usage.model_request_count,
            usage=usage,
        )


def _context_factory(
    request: AgentLaunchRequest,
    workspace_root: Path,
) -> Callable[[TurnKind, int, Sequence[ToolDefinition]], PromptBuildContext]:
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
) -> PermissionManager | None:
    if parent is None:
        return None
    return PermissionManager(
        getattr(parent, "_workspace"),
        tuple(getattr(parent, "_rule_sets")),
        parent.paths,
        set(getattr(parent, "_known_tool_names")),
        mode=mode,
        confirmer=lambda _: PermissionConfirmation.DENY,
    )


def _model_override(request: AgentLaunchRequest) -> str | None:
    if request.role is None:
        return None
    return request.role.model_policy.resolved_model


def _last_assistant_text(messages: tuple[ChatMessage, ...]) -> str:
    for message in reversed(messages):
        if message.role is Role.ASSISTANT and message.content:
            return message.content
    return ""


def _summary(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= 800:
        return stripped
    return stripped[:800] + "\n[摘要已截断。]"


def _failed_result(
    request: AgentLaunchRequest,
    message: str,
    usage: AgentUsage,
    session: ConversationSession,
) -> AgentTaskResult:
    return AgentTaskResult(
        task_id=request.task_id,
        kind=request.kind,
        role_name=request.role.name if request.role else None,
        status=AgentTaskStatus.FAILED,
        final_text=_last_assistant_text(session.messages),
        summary="子 Agent 任务执行失败。",
        error=message,
        rounds=usage.model_request_count,
        usage=usage,
    )


def _cancelled_result(
    request: AgentLaunchRequest,
    usage: AgentUsage,
    session: ConversationSession,
) -> AgentTaskResult:
    return AgentTaskResult(
        task_id=request.task_id,
        kind=request.kind,
        role_name=request.role.name if request.role else None,
        status=AgentTaskStatus.CANCELLED,
        final_text=_last_assistant_text(session.messages),
        summary="子 Agent 任务已取消。",
        error="用户取消了子 Agent 任务。",
        rounds=usage.model_request_count,
        usage=usage,
    )
