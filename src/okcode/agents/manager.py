"""子 Agent 后台任务管理。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from okcode.agents.models import (
    AgentExecutionMode,
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentTaskNotification,
    AgentTaskResult,
    AgentTaskSnapshot,
    AgentTaskStatus,
)


class AgentCancelToken:
    """跨线程可读的取消标记。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class AgentRunnerProtocol(Protocol):
    """任务管理器需要的子 Agent 运行入口。"""

    def run(
        self, request: AgentLaunchRequest, cancel_token: AgentCancelToken
    ) -> Awaitable[AgentTaskResult]: ...


@dataclass(slots=True)
class AgentTaskHandle:
    """后台任务句柄。"""

    task_id: str
    future: concurrent.futures.Future[AgentTaskResult] | None = None


@dataclass(slots=True)
class _TaskRecord:
    request: AgentLaunchRequest
    created_at: datetime
    cancel_token: AgentCancelToken
    status: AgentTaskStatus = AgentTaskStatus.QUEUED
    started_at: datetime | None = None
    ended_at: datetime | None = None
    result: AgentTaskResult | None = None
    error: str | None = None
    future: concurrent.futures.Future[AgentTaskResult] | None = None
    notify_on_finish: bool = False
    notified: bool = False
    tool_call_count: int = 0
    rounds: int = 0


class AgentBackgroundLoop:
    """运行后台子 Agent 的独立 asyncio loop。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._loop is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="okcode-agent-background",
                daemon=True,
            )
            self._thread.start()
        self._ready.wait(timeout=5)

    def submit(
        self, coro: Awaitable[AgentTaskResult]
    ) -> concurrent.futures.Future[AgentTaskResult]:
        self.start()
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def close(self, timeout_seconds: float = 2.0) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=timeout_seconds)
        self._loop = None
        self._thread = None
        self._ready.clear()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


class AgentTaskManager:
    """追踪子 Agent 任务状态、结果和通知。"""

    def __init__(
        self,
        runner: AgentRunnerProtocol,
        *,
        background_loop: AgentBackgroundLoop | None = None,
        auto_background_after: float = 2.0,
        retain_completed: int = 50,
    ) -> None:
        self._runner = runner
        self._background_loop = background_loop or AgentBackgroundLoop()
        self._auto_background_after = auto_background_after
        self._retain_completed = retain_completed
        self._records: dict[str, _TaskRecord] = {}
        self._notifications: list[AgentTaskNotification] = []
        self._lock = threading.RLock()

    async def run(self, request: AgentLaunchRequest) -> AgentTaskResult | AgentTaskSnapshot:
        """按请求执行方式运行任务。"""

        if (
            request.kind is AgentLaunchKind.FORK
            or request.execution_mode is AgentExecutionMode.BACKGROUND
        ):
            return self.start(request)
        if request.execution_mode is AgentExecutionMode.AUTO:
            return await self._run_auto(request)
        return await self._run_inline(request)

    def start(self, request: AgentLaunchRequest) -> AgentTaskSnapshot:
        """后台启动任务，并立即返回快照。"""

        record = self._create_record(request, notify_on_finish=True)
        record.status = AgentTaskStatus.BACKGROUND
        snapshot = self._snapshot(record)
        future = self._background_loop.submit(self._run_record(record))
        record.future = future
        future.add_done_callback(lambda item: self._collect_background_result(record, item))
        return snapshot

    async def _run_inline(self, request: AgentLaunchRequest) -> AgentTaskResult:
        record = self._create_record(request, notify_on_finish=False)
        return await self._run_record(record)

    async def _run_auto(self, request: AgentLaunchRequest) -> AgentTaskResult | AgentTaskSnapshot:
        record = self._create_record(request, notify_on_finish=False)
        record.status = AgentTaskStatus.BACKGROUND
        future = self._background_loop.submit(self._run_record(record))
        record.future = future
        future.add_done_callback(lambda item: self._collect_background_result(record, item))
        try:
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=self._auto_background_after,
            )
        except TimeoutError:
            with self._lock:
                record.notify_on_finish = True
                record.status = AgentTaskStatus.BACKGROUND
                return self._snapshot(record)

    def move_to_background(self, task_id: str) -> AgentTaskSnapshot:
        """幂等地把任务标记为后台通知。"""

        with self._lock:
            record = self._require_record(task_id)
            record.notify_on_finish = True
            if record.status in {AgentTaskStatus.QUEUED, AgentTaskStatus.RUNNING}:
                record.status = AgentTaskStatus.BACKGROUND
            return self._snapshot(record)

    def cancel(self, task_id: str) -> AgentTaskSnapshot:
        """取消运行中的任务。"""

        with self._lock:
            record = self._require_record(task_id)
            if record.result is not None:
                return self._snapshot(record)
            record.cancel_token.cancel()
            if record.future is not None:
                record.future.cancel()
            result = AgentTaskResult(
                task_id=record.request.task_id,
                kind=record.request.kind,
                role_name=record.request.role.name if record.request.role else None,
                status=AgentTaskStatus.CANCELLED,
                summary="子 Agent 任务已取消。",
                error="用户取消了子 Agent 任务。",
                started_at=record.started_at,
                ended_at=_now(),
                isolation=record.request.isolation,
            )
            self._finish_record(record, result)
            return self._snapshot(record)

    def list_snapshots(self) -> tuple[AgentTaskSnapshot, ...]:
        with self._lock:
            return tuple(self._snapshot(record) for record in self._records.values())

    def get_snapshot(self, task_id: str) -> AgentTaskSnapshot:
        with self._lock:
            return self._snapshot(self._require_record(task_id))

    def drain_notifications(self, parent_session_id: str) -> tuple[AgentTaskNotification, ...]:
        with self._lock:
            matched = [
                item for item in self._notifications if item.parent_session_id == parent_session_id
            ]
            self._notifications = [
                item for item in self._notifications if item.parent_session_id != parent_session_id
            ]
            return tuple(matched)

    def close(self) -> None:
        self._background_loop.close()

    def _create_record(self, request: AgentLaunchRequest, *, notify_on_finish: bool) -> _TaskRecord:
        if not request.task_id:
            request = _replace_task_id(request, str(uuid.uuid4()))
        record = _TaskRecord(request, _now(), AgentCancelToken(), notify_on_finish=notify_on_finish)
        with self._lock:
            self._records[request.task_id] = record
        return record

    async def _run_record(self, record: _TaskRecord) -> AgentTaskResult:
        with self._lock:
            if record.result is not None:
                return record.result
            if record.status is AgentTaskStatus.QUEUED:
                record.status = AgentTaskStatus.RUNNING
            record.started_at = _now()
        try:
            coro = self._runner.run(record.request, record.cancel_token)
            if record.request.timeout_seconds is not None:
                result = await asyncio.wait_for(coro, timeout=record.request.timeout_seconds)
            else:
                result = await coro
            if record.cancel_token.cancelled and result.status not in {
                AgentTaskStatus.CANCELLED,
                AgentTaskStatus.TIMED_OUT,
            }:
                result = _result_with_status(
                    record.request,
                    AgentTaskStatus.CANCELLED,
                    "子 Agent 任务已取消。",
                    "用户取消了子 Agent 任务。",
                    record.started_at,
                )
        except TimeoutError:
            result = _result_with_status(
                record.request,
                AgentTaskStatus.TIMED_OUT,
                "子 Agent 任务执行超时。",
                "子 Agent 任务达到超时限制。",
                record.started_at,
            )
        except asyncio.CancelledError:
            result = _result_with_status(
                record.request,
                AgentTaskStatus.CANCELLED,
                "子 Agent 任务已取消。",
                "用户取消了子 Agent 任务。",
                record.started_at,
            )
        except Exception as exc:
            result = _result_with_status(
                record.request,
                AgentTaskStatus.FAILED,
                "子 Agent 任务执行失败。",
                str(exc),
                record.started_at,
            )
        self._finish_record(record, result)
        return result

    def _collect_background_result(
        self,
        record: _TaskRecord,
        future: concurrent.futures.Future[AgentTaskResult],
    ) -> None:
        if record.result is not None:
            return
        if future.cancelled():
            self._finish_record(
                record,
                _result_with_status(
                    record.request,
                    AgentTaskStatus.CANCELLED,
                    "子 Agent 任务已取消。",
                    "用户取消了子 Agent 任务。",
                    record.started_at,
                ),
            )
            return
        try:
            result = future.result()
        except Exception as exc:
            result = _result_with_status(
                record.request,
                AgentTaskStatus.FAILED,
                "子 Agent 任务执行失败。",
                str(exc),
                record.started_at,
            )
        self._finish_record(record, result)

    def _finish_record(self, record: _TaskRecord, result: AgentTaskResult) -> None:
        with self._lock:
            if record.result is not None:
                return
            if result.ended_at is None:
                result = AgentTaskResult(
                    task_id=result.task_id,
                    kind=result.kind,
                    status=result.status,
                    role_name=result.role_name,
                    final_text=result.final_text,
                    summary=result.summary,
                    full_result_ref=result.full_result_ref,
                    error=result.error,
                    rounds=result.rounds,
                    tool_calls=result.tool_calls,
                    usage=result.usage,
                    started_at=result.started_at or record.started_at,
                    ended_at=_now(),
                    isolation=result.isolation,
                    worktree=result.worktree,
                )
            record.result = result
            record.status = result.status
            record.error = result.error
            record.rounds = result.rounds
            record.tool_call_count = len(result.tool_calls) or result.usage.tool_call_count
            record.ended_at = result.ended_at
            if record.notify_on_finish and not record.notified:
                self._notifications.append(
                    AgentTaskNotification(record.request.parent_session_id, result)
                )
                record.notified = True
            self._trim_completed_locked()

    def _trim_completed_locked(self) -> None:
        completed = [item for item in self._records.values() if item.result is not None]
        overflow = len(completed) - self._retain_completed
        if overflow <= 0:
            return
        for record in sorted(completed, key=lambda item: item.ended_at or item.created_at)[
            :overflow
        ]:
            self._records.pop(record.request.task_id, None)

    def _snapshot(self, record: _TaskRecord) -> AgentTaskSnapshot:
        result = record.result
        usage = result.usage if result is not None else None
        now = _now()
        end = record.ended_at or now
        start = record.started_at or record.created_at
        return AgentTaskSnapshot(
            task_id=record.request.task_id,
            kind=record.request.kind,
            role_name=record.request.role.name if record.request.role else None,
            status=record.status,
            created_at=record.created_at,
            started_at=record.started_at,
            ended_at=record.ended_at,
            elapsed_seconds=max(0.0, (end - start).total_seconds()),
            rounds=record.rounds,
            tool_call_count=record.tool_call_count,
            usage=usage or result_usage(record),
            summary=result.summary if result is not None else "",
            error=record.error,
            isolation=result.isolation if result is not None else record.request.isolation,
            worktree=result.worktree if result is not None else None,
        )

    def _require_record(self, task_id: str) -> _TaskRecord:
        try:
            return self._records[task_id]
        except KeyError as exc:
            raise LookupError(f"不存在任务：{task_id}") from exc


def result_usage(record: _TaskRecord):
    from okcode.agents.models import AgentUsage

    return AgentUsage(tool_call_count=record.tool_call_count, model_request_count=record.rounds)


def _result_with_status(
    request: AgentLaunchRequest,
    status: AgentTaskStatus,
    summary: str,
    error: str | None,
    started_at: datetime | None,
) -> AgentTaskResult:
    return AgentTaskResult(
        task_id=request.task_id,
        kind=request.kind,
        role_name=request.role.name if request.role else None,
        status=status,
        summary=summary,
        error=error,
        started_at=started_at,
        ended_at=_now(),
        isolation=request.isolation,
    )


def _replace_task_id(request: AgentLaunchRequest, task_id: str) -> AgentLaunchRequest:
    return AgentLaunchRequest(
        task_id=task_id,
        kind=request.kind,
        task=request.task,
        parent_session_id=request.parent_session_id,
        role=request.role,
        parent_messages=request.parent_messages,
        parent_tool_names=request.parent_tool_names,
        visible_tool_names=request.visible_tool_names,
        tool_denied_reasons=dict(request.tool_denied_reasons),
        execution_mode=request.execution_mode,
        timeout_seconds=request.timeout_seconds,
        max_turns=request.max_turns,
        depth=request.depth,
        trigger=request.trigger,
        runtime_mode=request.runtime_mode,
        permission_mode=request.permission_mode,
        isolation=request.isolation,
        worktree_request=request.worktree_request,
        main_workspace_root=request.main_workspace_root,
    )


def _now() -> datetime:
    return datetime.now(UTC)
