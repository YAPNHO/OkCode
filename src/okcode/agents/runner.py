"""隔离子 Agent 运行器。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from okcode.agents.manager import AgentCancelToken
from okcode.agents.models import (
    AgentIsolationMode,
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentTaskResult,
    AgentTaskStatus,
    AgentUsage,
)
from okcode.agents.runtime import build_child_runtime
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
from okcode.prompt import PromptCachePolicy
from okcode.providers.base import LLMProvider
from okcode.tools.registry import ToolRegistry
from okcode.worktrees.manager import WorktreeManager
from okcode.worktrees.models import (
    GitStatusSummary,
    WorktreeCleanupStatus,
    WorktreeExitReport,
)

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
        worktree_manager: WorktreeManager | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._registry = registry
        self._workspace_root = workspace_root
        self._cache_policy = cache_policy or PromptCachePolicy()
        self._parent_permissions = parent_permissions
        self._worktree_manager = worktree_manager

    async def run(
        self,
        request: AgentLaunchRequest,
        cancel_token: AgentCancelToken,
    ) -> AgentTaskResult:
        """执行子 Agent 并汇总可返回给父对话的结果。"""

        usage = AgentUsage()
        lease = None
        worktree_report: WorktreeExitReport | None = None
        workspace_root = self._workspace_root
        if request.isolation is AgentIsolationMode.WORKTREE:
            if self._worktree_manager is None or request.worktree_request is None:
                return _failed_result(
                    request,
                    "子 Agent 请求 worktree 隔离，但运行器未配置 WorktreeManager。",
                    usage,
                    None,
                )
            try:
                lease = self._worktree_manager.prepare(request.worktree_request)
                workspace_root = lease.path
            except Exception as exc:
                return _failed_result(request, str(exc), usage, None)

        provider = self._provider_factory(_model_override(request))
        runtime = build_child_runtime(
            request,
            self._registry,
            workspace_root=workspace_root,
            parent_permissions=self._parent_permissions,
            worktree_note=lease.prompt_note if lease else None,
        )
        session = ConversationSession(
            provider,
            runtime.registry,
            runtime.executor,
            config=AgentConfig(max_iterations=request.max_turns),
            cache_policy=self._cache_policy,
            permissions=runtime.permissions,
            context_manager=runtime.context_manager,
            context_factory=runtime.context_factory,
            workspace_root=runtime.workspace_root,
            model_name=_model_override(request) or "",
            initial_messages=request.parent_messages
            if request.kind is AgentLaunchKind.FORK
            else (),
        )

        stopped: AgentStopped | None = None
        try:
            async for event in session.stream_user_message(request.task):
                if cancel_token.cancelled:
                    worktree_report = _finalize_worktree(self._worktree_manager, lease)
                    return _with_worktree_result(
                        _cancelled_result(request, usage, session),
                        request,
                        worktree_report,
                    )
                if isinstance(event, TokenUsageReported):
                    usage = usage.add_token_usage(event.usage)
                elif isinstance(event, ToolExecutionFinished):
                    usage = usage.add_tool_calls(1)
                elif isinstance(event, AgentStopped):
                    stopped = event
        except ProviderError as exc:
            worktree_report = _finalize_worktree(self._worktree_manager, lease)
            return _with_worktree_result(
                _failed_result(request, exc.safe_message, usage, session),
                request,
                worktree_report,
            )
        except Exception as exc:
            worktree_report = _finalize_worktree(self._worktree_manager, lease)
            return _with_worktree_result(
                _failed_result(request, str(exc), usage, session),
                request,
                worktree_report,
            )

        final_text = _last_assistant_text(session.messages)
        worktree_report = _finalize_worktree(self._worktree_manager, lease)
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
                isolation=request.isolation,
                worktree=worktree_report,
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
            isolation=request.isolation,
            worktree=worktree_report,
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
    session: ConversationSession | None,
) -> AgentTaskResult:
    return AgentTaskResult(
        task_id=request.task_id,
        kind=request.kind,
        role_name=request.role.name if request.role else None,
        status=AgentTaskStatus.FAILED,
        final_text=_last_assistant_text(session.messages) if session else "",
        summary="子 Agent 任务执行失败。",
        error=message,
        rounds=usage.model_request_count,
        usage=usage,
        isolation=request.isolation,
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
        isolation=request.isolation,
    )


def _finalize_worktree(
    manager: WorktreeManager | None,
    lease,
) -> WorktreeExitReport | None:
    if manager is None or lease is None:
        return None
    try:
        return manager.finalize(lease)
    except Exception as exc:
        return WorktreeExitReport(
            path=lease.path,
            branch=lease.branch,
            name=lease.metadata.identity.name,
            status_summary=GitStatusSummary(failed=True, error=str(exc)),
            changed_files=(),
            protection_reasons=(),
            cleanup_decision=WorktreeCleanupStatus.FAILED,
            cleanup_message=f"worktree 退出清理失败：{exc}",
        )


def _with_worktree_result(
    result: AgentTaskResult,
    request: AgentLaunchRequest,
    worktree_report: WorktreeExitReport | None,
) -> AgentTaskResult:
    return replace(result, isolation=request.isolation, worktree=worktree_report)
