"""团队协作编排入口。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from okcode.teams.backends import (
    BackendSelector,
    CoroutineBackend,
    TeamBackend,
    TerminalPaneBackend,
    delivery_from_wake,
)
from okcode.teams.mailbox import MailboxStore
from okcode.teams.models import (
    ApprovalDecision,
    ApprovalRequest,
    BackendPreference,
    BroadcastReport,
    MessageDeliveryReport,
    NameRegistryEntry,
    TeamBackendKind,
    TeamMember,
    TeamMemberStatus,
    TeamMergeReport,
    TeamMergeRequest,
    TeamMergeStatus,
    TeamMessage,
    TeamMessageProtocol,
    TeamMetadata,
    TeamSnapshot,
    TeamStatus,
    TeamTask,
    TeamTaskStatus,
    utc_now,
)
from okcode.teams.naming import validate_member_name, validate_team_name
from okcode.teams.paths import default_teams_root
from okcode.teams.store import TeamStore


class TeamRuntime:
    """长期团队的统一运行时。"""

    def __init__(
        self,
        store: TeamStore | None = None,
        mailbox: MailboxStore | None = None,
        selector: BackendSelector | None = None,
        backends: tuple[TeamBackend, ...] | None = None,
        merge_manager: object | None = None,
        worker_factory: Callable[[str, str, str | None], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store or TeamStore(default_teams_root())
        self._mailbox = mailbox or MailboxStore()
        self._selector = selector or BackendSelector()
        self._backends = backends or (TerminalPaneBackend(), CoroutineBackend())
        self._merge_manager = merge_manager
        self._worker_factory = worker_factory
        self._worker_lock = threading.Lock()
        self._active_workers: set[tuple[str, str]] = set()
        self._worker_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

    @property
    def store(self) -> TeamStore:
        return self._store

    @property
    def mailbox(self) -> MailboxStore:
        return self._mailbox

    def configure_worker_factory(
        self,
        factory: Callable[[str, str, str | None], Awaitable[None]] | None,
    ) -> None:
        """设置成员唤醒后的异步执行入口。

        团队状态层不直接依赖 AgentRunner，CLI 在完成 provider 和工具装配后注入
        worker 工厂。这样测试可以使用本地替身，普通会话也不会被团队执行逻辑污染。
        """

        self._worker_factory = factory

    def create_team(self, name: str, leader_session_id: str) -> TeamSnapshot:
        safe_name = validate_team_name(name)
        paths = self._store.paths(safe_name)
        metadata = TeamMetadata(
            version=1,
            name=safe_name,
            leader_session_id=leader_session_id,
            root_path=paths.root,
            status=TeamStatus.ACTIVE,
        )
        snapshot = self._store.create(metadata)
        self._ensure_lead_registry(safe_name)
        return self._store.load(snapshot.metadata.name)

    def use_team(self, name: str, leader_session_id: str | None = None) -> TeamSnapshot:
        snapshot = self._store.load(name)
        if leader_session_id is None or snapshot.metadata.leader_session_id == leader_session_id:
            self._ensure_lead_registry(snapshot.metadata.name)
            return self._store.load(snapshot.metadata.name)
        metadata = replace(
            snapshot.metadata,
            leader_session_id=leader_session_id,
            updated_at=utc_now(),
        )
        self._store.create(metadata)
        self._ensure_lead_registry(name)
        return self._store.load(name)

    def snapshot(self, team_name: str) -> TeamSnapshot:
        return self._store.load(team_name)

    def add_member(
        self,
        team_name: str,
        *,
        name: str,
        role: str,
        workdir: Path,
        approval_required: bool = False,
        backend_preference: BackendPreference | None = None,
    ) -> TeamMember:
        safe_member = validate_member_name(name)
        preference = backend_preference or BackendPreference()
        selection = self._selector.select(preference, self._backends)
        paths = self._store.paths(team_name)
        member = TeamMember(
            name=safe_member,
            role=role,
            workdir=workdir.resolve(),
            backend=selection.kind,
            mailbox_path=paths.mailbox_path(safe_member),
            approval_required=approval_required,
        )
        backend = self._backend(selection.kind)
        handle = backend.spawn(member)
        member = replace(member, backend_handle=handle, status=TeamMemberStatus.IDLE)
        stored = self._store.upsert_member(team_name, member)
        return stored

    def create_task(
        self,
        team_name: str,
        *,
        title: str,
        body: str,
        owner: str | None = None,
        dependencies: tuple[str, ...] = (),
    ) -> TeamTask:
        task = TeamTask(
            task_id=self._store.new_task_id(),
            title=title,
            body=body,
            owner=owner,
            dependencies=tuple(dependencies),
            status=TeamTaskStatus.TODO,
        )

        def mutate(tasks: list[TeamTask]) -> list[TeamTask]:
            return [*tasks, task]

        self._store.mutate_tasks(team_name, mutate)
        return task

    def update_task(self, team_name: str, task_id: str, **patch: object) -> TeamTask:
        updated: TeamTask | None = None

        def mutate(tasks: list[TeamTask]) -> list[TeamTask]:
            nonlocal updated
            result = []
            for task in tasks:
                if task.task_id == task_id:
                    values = {key: value for key, value in patch.items() if value is not None}
                    if "status" in values and isinstance(values["status"], str):
                        values["status"] = TeamTaskStatus(values["status"])
                    updated = replace(task, **values, updated_at=utc_now())
                    result.append(updated)
                else:
                    result.append(task)
            return result

        self._store.mutate_tasks(team_name, mutate)
        if updated is None:
            raise LookupError(f"任务不存在：{task_id}")
        return updated

    def list_tasks(self, team_name: str) -> tuple[TeamTask, ...]:
        return self._store.list_tasks(team_name)

    def send_message(
        self,
        team_name: str,
        sender: str,
        recipient: str,
        message: TeamMessage,
    ) -> MessageDeliveryReport:
        registry = self._store.read_registry(team_name)
        entry = registry.get(recipient)
        if entry is None:
            return MessageDeliveryReport(recipient, "failed", error=f"目标成员不存在：{recipient}")
        stored = self._mailbox.append(
            entry.mailbox_path,
            replace(message, sender=sender, recipient=recipient),
        )
        if entry.backend is TeamBackendKind.TERMINAL_PANE and entry.backend_handle is not None:
            wake = self._backend(entry.backend).wake(entry.backend_handle, stored.message_id)
            report = delivery_from_wake(recipient, stored.message_id, entry.mailbox_path, wake)
            if wake.woken:
                self._schedule_worker(team_name, recipient, stored.message_id)
            return report
        self._schedule_worker(team_name, recipient, stored.message_id)
        return MessageDeliveryReport(
            recipient=recipient,
            status="delivered",
            message_id=stored.message_id,
            mailbox_path=entry.mailbox_path,
        )

    def broadcast(
        self,
        team_name: str,
        sender: str,
        message: TeamMessage,
        *,
        include_sender: bool = False,
    ) -> BroadcastReport:
        results = []
        for entry in self._store.read_registry(team_name).entries:
            if not include_sender and entry.name == sender:
                continue
            results.append(
                self.send_message(
                    team_name,
                    sender,
                    entry.name,
                    replace(message, protocol=TeamMessageProtocol.BROADCAST),
                )
            )
        return BroadcastReport(tuple(results))

    def create_approval_request(
        self,
        team_name: str,
        member_name: str,
        task_id: str,
        plan: str,
        risk_summary: str = "",
    ) -> TeamMessage:
        request = ApprovalRequest(
            request_id=f"approval-{utc_now().timestamp():.0f}",
            member_name=member_name,
            task_id=task_id,
            plan=plan,
            risk_summary=risk_summary,
        )
        self._store.update_member_status(team_name, member_name, TeamMemberStatus.WAITING_APPROVAL)
        return TeamMessage(
            sender=member_name,
            recipient="lead",
            body=plan,
            protocol=TeamMessageProtocol.APPROVAL_REQUEST,
            task_id=task_id,
            payload={
                "request_id": request.request_id,
                "risk_summary": request.risk_summary,
            },
        )

    def create_approval_decision(
        self,
        request_id: str,
        approved: bool,
        reason: str,
        constraints: tuple[str, ...] = (),
    ) -> TeamMessage:
        decision = ApprovalDecision(request_id, approved, reason, constraints)
        return TeamMessage(
            sender="lead",
            recipient="",
            body=reason,
            protocol=TeamMessageProtocol.APPROVAL_DECISION,
            payload={
                "request_id": decision.request_id,
                "approved": decision.approved,
                "constraints": list(decision.constraints),
            },
        )

    def wake_member(self, team_name: str, member_name: str) -> MessageDeliveryReport:
        entry = self._require_entry(team_name, member_name)
        if entry.backend_handle is None:
            return MessageDeliveryReport(entry.name, "failed", error="成员缺少后端 handle。")
        wake = self._backend(entry.backend).wake(entry.backend_handle)
        if wake.woken:
            self._schedule_worker(team_name, entry.name, None)
        return MessageDeliveryReport(
            entry.name,
            wake.status,
            error=None if wake.woken else wake.message,
            woken=wake.woken,
        )

    async def wake_member_async(
        self,
        team_name: str,
        member_name: str,
        *,
        timeout_seconds: float = 540.0,
    ) -> MessageDeliveryReport:
        """唤醒成员并等待当前这次后台执行结束。

        `send_message` 仍然保持非阻塞；模型显式调用 `team_member wake` 时等待成员完成，
        这样同一轮 Lead 可以继续读取完成消息并汇总结果。
        """

        report = self.wake_member(team_name, member_name)
        if not report.woken:
            return report
        key = (team_name, validate_member_name(member_name))
        with self._worker_lock:
            task = self._worker_tasks.get(key)
        if task is None:
            return report
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except TimeoutError:
            return report
        member = next(
            (
                item
                for item in self._store.load(team_name).members
                if item.name == member_name
            ),
            None,
        )
        return replace(report, status=member.status.value if member is not None else report.status)

    def terminate_member(self, team_name: str, member_name: str) -> MessageDeliveryReport:
        entry = self._require_entry(team_name, member_name)
        if entry.backend_handle is None:
            return MessageDeliveryReport(entry.name, "failed", error="成员缺少后端 handle。")
        report = self._backend(entry.backend).terminate(entry.backend_handle)
        self._store.update_member_status(team_name, entry.name, TeamMemberStatus.TERMINATED)
        return MessageDeliveryReport(entry.name, report.status, error=None)

    def restore_member(self, team_name: str, member_name: str) -> MessageDeliveryReport:
        snapshot = self._store.load(team_name)
        member = next((item for item in snapshot.members if item.name == member_name), None)
        if member is None:
            return MessageDeliveryReport(member_name, "failed", error=f"成员不存在：{member_name}")
        if member.context_ref is None:
            self._store.update_member_status(
                team_name,
                member_name,
                TeamMemberStatus.UNRECOVERABLE,
                error="缺少上下文引用。",
            )
            return MessageDeliveryReport(member_name, "failed", error="缺少上下文引用。")
        if not member.workdir.exists():
            self._store.update_member_status(
                team_name,
                member_name,
                TeamMemberStatus.UNRECOVERABLE,
                error="工作目录不存在。",
            )
            return MessageDeliveryReport(member_name, "failed", error="工作目录不存在。")
        return self.wake_member(team_name, member_name)

    def _schedule_worker(
        self,
        team_name: str,
        member_name: str,
        message_id: str | None,
    ) -> None:
        factory = self._worker_factory
        if factory is None or member_name == "lead":
            return
        key = (team_name, member_name)
        with self._worker_lock:
            if key in self._active_workers:
                return
            self._active_workers.add(key)

        async def run() -> None:
            try:
                await factory(team_name, member_name, message_id)
            finally:
                with self._worker_lock:
                    self._active_workers.discard(key)
                    self._worker_tasks.pop(key, None)
                if self._has_pending_worker_work(team_name, member_name):
                    self._schedule_worker(team_name, member_name, None)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 终端/同步调用方没有现成事件循环时，仍然异步启动一次独立 worker。
            thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
            thread.start()
        else:
            task = loop.create_task(run())
            with self._worker_lock:
                self._worker_tasks[key] = task

    def _has_pending_worker_work(self, team_name: str, member_name: str) -> bool:
        """判断成员是否还有未消费消息或待执行任务。"""

        try:
            snapshot = self._store.load(team_name)
        except (FileNotFoundError, ValueError):
            return False
        member = next((item for item in snapshot.members if item.name == member_name), None)
        if member is None or member.status in {
            TeamMemberStatus.WAITING_APPROVAL,
            TeamMemberStatus.BLOCKED,
            TeamMemberStatus.FAILED,
            TeamMemberStatus.TERMINATED,
        }:
            return False
        unread = self._mailbox.unread(member.mailbox_path)
        if any(
            message.task_id is not None
            or message.protocol
            in {
                TeamMessageProtocol.TASK_ASSIGNMENT,
                TeamMessageProtocol.APPROVAL_DECISION,
                TeamMessageProtocol.RESUME,
            }
            for message in unread
        ):
            return True
        return any(
            task.owner == member_name and task.status in {TeamTaskStatus.TODO, TeamTaskStatus.READY}
            for task in snapshot.tasks
        )

    def merge(self, team_name: str, request: TeamMergeRequest) -> TeamMergeReport:
        if self._merge_manager is None:
            return TeamMergeReport(TeamMergeStatus.FAILED, message="团队合并管理器未启用。")
        return self._merge_manager.merge(request)

    def _backend(self, kind: TeamBackendKind) -> TeamBackend:
        for backend in self._backends:
            if backend.kind is kind:
                return backend
        raise LookupError(f"后端未注册：{kind.value}")

    def _require_entry(self, team_name: str, member_name: str) -> NameRegistryEntry:
        entry = self._store.read_registry(team_name).get(validate_member_name(member_name))
        if entry is None:
            raise LookupError(f"成员不存在：{member_name}")
        return entry

    def _ensure_lead_registry(self, team_name: str) -> None:
        paths = self._store.paths(team_name)
        mailbox_path = paths.mailbox_path("lead")
        mailbox_path.parent.mkdir(parents=True, exist_ok=True)
        mailbox_path.touch(exist_ok=True)
        if self._store.read_registry(team_name).get("lead") is not None:
            return
        self._store.update_registry(
            team_name,
            NameRegistryEntry(
                "lead",
                mailbox_path,
                TeamBackendKind.COROUTINE,
                TeamMemberStatus.IDLE,
            ),
        )
