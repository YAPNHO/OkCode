"""长期团队成员 worker。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from okcode.agents.manager import AgentCancelToken
from okcode.agents.models import (
    AgentExecutionMode,
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentTaskResult,
    AgentTaskStatus,
)
from okcode.agents.runner import AgentRunner
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import PermissionMode
from okcode.prompt import PromptCachePolicy
from okcode.providers.base import LLMProvider
from okcode.teams.models import (
    MemberContextRef,
    TeamActorKind,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageProtocol,
    TeamTask,
    TeamToolContext,
)
from okcode.teams.runtime import TeamRuntime
from okcode.teams.serialization import to_jsonable
from okcode.tools.defaults import build_team_registry
from okcode.tools.registry import ToolRegistry

ProviderFactory = Callable[[str | None], LLMProvider]


@dataclass(slots=True)
class TeamWorkerApp:
    """读取成员邮箱、执行任务并向 Lead 回传结果。"""

    runtime: TeamRuntime
    team_name: str
    member_name: str
    workspace_root: Path
    provider_factory: ProviderFactory | None = None
    registry: ToolRegistry | None = None
    parent_permissions: PermissionManager | None = None
    cache_policy: PromptCachePolicy | None = None
    max_turns: int = 8

    def run_once(self) -> int:
        """同步入口，供独立终端 worker 使用。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_once_async())
        raise RuntimeError(
            "TeamWorkerApp.run_once() 不能在运行中的事件循环内调用，请使用 run_once_async()。"
        )

    async def run_once_async(self, trigger_message_id: str | None = None) -> int:
        """处理一次当前成员邮箱中的任务。"""

        if self.provider_factory is None or self.registry is None:
            report = self.runtime.restore_member(self.team_name, self.member_name)
            return 0 if report.status != "failed" else 1

        snapshot = self.runtime.snapshot(self.team_name)
        member = next((item for item in snapshot.members if item.name == self.member_name), None)
        if member is None:
            return 1
        unread = self.runtime.mailbox.unread(member.mailbox_path)
        task = self._select_task(snapshot.tasks, unread)
        if task is None:
            return 0

        decision = self._approval_decision(task.task_id, unread)
        if member.approval_required and member.status is TeamMemberStatus.WAITING_APPROVAL:
            if decision is None:
                return self._mark_read(member.mailbox_path, self._task_messages(task, unread))
        elif member.approval_required and decision is None:
            self.runtime.store.update_member_status(
                self.team_name,
                self.member_name,
                TeamMemberStatus.WAITING_APPROVAL,
            )
            request = self.runtime.create_approval_request(
                self.team_name,
                self.member_name,
                task.task_id,
                plan=f"任务：{task.title}\n{task.body}",
            )
            self.runtime.send_message(
                self.team_name,
                self.member_name,
                "lead",
                request,
            )
            return self._mark_read(member.mailbox_path, self._task_messages(task, unread))
        elif member.approval_required and decision is not None and not _approved(decision):
            self.runtime.update_task(
                self.team_name,
                task.task_id,
                status="blocked",
                blocked_reason=decision.body or "Lead 驳回了成员执行请求。",
            )
            self.runtime.store.update_member_status(
                self.team_name,
                self.member_name,
                TeamMemberStatus.BLOCKED,
                error=decision.body or "Lead 驳回了执行请求。",
            )
            self._send_status(
                task.task_id,
                TeamMessageProtocol.BLOCKED,
                decision.body or "Lead 驳回了执行请求。",
            )
            return self._mark_read(member.mailbox_path, self._task_messages(task, unread))

        self.runtime.update_task(self.team_name, task.task_id, status="running")
        self.runtime.store.update_member_status(
            self.team_name,
            self.member_name,
            TeamMemberStatus.RUNNING,
        )
        try:
            result = await self._run_agent(task)
        except Exception as exc:
            summary = f"成员执行失败：{exc}"
            self.runtime.update_task(
                self.team_name,
                task.task_id,
                status="failed",
                output_summary=summary,
            )
            self.runtime.store.update_member_status(
                self.team_name,
                self.member_name,
                TeamMemberStatus.FAILED,
                error=str(exc),
            )
            self._send_status(task.task_id, TeamMessageProtocol.BLOCKED, summary)
            return self._mark_read(member.mailbox_path, self._task_messages(task, unread))

        success = result.status is AgentTaskStatus.COMPLETED
        summary = result.summary or result.final_text or result.error or "成员未返回文本结果。"
        status = "done" if success else "failed"
        self.runtime.update_task(
            self.team_name,
            task.task_id,
            status=status,
            output_summary=summary,
        )
        context_ref = self._save_context(task, summary, trigger_message_id, member.backend)
        self.runtime.store.update_member_status(
            self.team_name,
            self.member_name,
            TeamMemberStatus.IDLE if success else TeamMemberStatus.FAILED,
            context_ref=context_ref,
            error=None if success else result.error or summary,
        )
        self._send_status(
            task.task_id,
            TeamMessageProtocol.COMPLETION if success else TeamMessageProtocol.BLOCKED,
            summary,
        )
        return self._mark_read(member.mailbox_path, self._task_messages(task, unread))

    async def _run_agent(self, task: TeamTask) -> AgentTaskResult:
        context = TeamToolContext(
            self.team_name,
            self.member_name,
            TeamActorKind.MEMBER,
        )
        registry = build_team_registry(
            self.registry,
            runtime=self.runtime,
            context=context,
        )
        request = AgentLaunchRequest(
            task_id=f"team-{self.team_name}-{task.task_id}",
            kind=AgentLaunchKind.DEFINED,
            task=(
                f"你是长期团队成员 {self.member_name}，负责完成下面的共享任务。"
                "不要向用户提问；完成后给出简明、可核查的结果摘要。\n\n"
                f"任务标题：{task.title}\n任务内容：{task.body}"
            ),
            parent_session_id=f"team:{self.team_name}:{self.member_name}",
            parent_tool_names=tuple(item.name for item in registry.definitions()),
            visible_tool_names=tuple(item.name for item in registry.definitions()),
            execution_mode=AgentExecutionMode.FOREGROUND,
            max_turns=self.max_turns,
            permission_mode=(
                self.parent_permissions.mode
                if self.parent_permissions is not None
                else PermissionMode.DEFAULT
            ),
        )
        runner = AgentRunner(
            self.provider_factory,
            registry,
            workspace_root=self.workspace_root,
            cache_policy=self.cache_policy,
            parent_permissions=self.parent_permissions,
        )
        return await runner.run(request, AgentCancelToken())

    def _select_task(
        self,
        tasks: tuple[TeamTask, ...],
        messages: tuple[TeamMessage, ...],
    ) -> TeamTask | None:
        task_ids = {message.task_id for message in messages if message.task_id}
        for task in tasks:
            if task.owner == self.member_name and task.task_id in task_ids:
                return task
        for task in tasks:
            if task.owner == self.member_name and task.status.value in {"todo", "ready"}:
                return task
        return None

    @staticmethod
    def _approval_decision(task_id: str, messages: tuple[TeamMessage, ...]) -> TeamMessage | None:
        return next(
            (
                message
                for message in messages
                if message.protocol is TeamMessageProtocol.APPROVAL_DECISION
                and message.task_id == task_id
            ),
            None,
        )

    @staticmethod
    def _task_messages(
        task: TeamTask,
        messages: tuple[TeamMessage, ...],
    ) -> tuple[TeamMessage, ...]:
        """只消费当前任务的消息，避免并行任务互相吞掉邮箱记录。"""

        matched = tuple(message for message in messages if message.task_id == task.task_id)
        if matched:
            return matched
        return tuple(message for message in messages if message.task_id is None)

    def _send_status(self, task_id: str, protocol: TeamMessageProtocol, body: str) -> None:
        self.runtime.send_message(
            self.team_name,
            self.member_name,
            "lead",
            TeamMessage(
                sender=self.member_name,
                recipient="lead",
                body=body,
                protocol=protocol,
                task_id=task_id,
                payload={"member": self.member_name, "task_id": task_id},
            ),
        )

    def _save_context(
        self,
        task: TeamTask,
        summary: str,
        trigger_message_id: str | None,
        backend,
    ) -> MemberContextRef:
        paths = self.runtime.store.paths(self.team_name)
        path = paths.member_sessions_dir / f"{self.member_name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "task_id": task.task_id,
            "member": self.member_name,
            "summary": summary,
        }
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(to_jsonable(record), ensure_ascii=False) + "\n")
        return MemberContextRef(
            session_id=f"team-{self.team_name}-{self.member_name}",
            journal_path=path,
            workspace_root=self.workspace_root,
            backend_kind=backend,
            last_message_id=trigger_message_id,
        )

    def _mark_read(self, mailbox_path: Path, messages: tuple[TeamMessage, ...]) -> int:
        ids = tuple(message.message_id for message in messages if message.message_id)
        if ids:
            self.runtime.mailbox.mark_read(mailbox_path, ids)
        return 0


def _approved(message: TeamMessage) -> bool:
    return bool(message.payload.get("approved", False))


def run_team_worker(
    runtime: TeamRuntime,
    *,
    team_name: str,
    member_name: str,
    workspace_root: Path,
) -> int:
    """供独立终端命令调用的兼容入口。"""

    return TeamWorkerApp(runtime, team_name, member_name, workspace_root).run_once()
