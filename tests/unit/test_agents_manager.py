from __future__ import annotations

import asyncio

from okcode.agents.manager import AgentCancelToken, AgentTaskManager
from okcode.agents.models import (
    AgentExecutionMode,
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentTaskResult,
    AgentTaskStatus,
)


class ControlledRunner:
    def __init__(self, *, delay: float = 0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.started = 0
        self.cancel_seen = False

    async def run(
        self, request: AgentLaunchRequest, cancel_token: AgentCancelToken
    ) -> AgentTaskResult:
        self.started += 1
        if self.fail:
            raise RuntimeError("boom")
        if self.delay:
            await asyncio.sleep(self.delay)
        if cancel_token.cancelled:
            self.cancel_seen = True
            return AgentTaskResult(
                request.task_id,
                request.kind,
                AgentTaskStatus.CANCELLED,
                summary="cancelled",
            )
        return AgentTaskResult(
            request.task_id,
            request.kind,
            AgentTaskStatus.COMPLETED,
            summary="done",
            final_text="完成",
            rounds=1,
            tool_calls=("read_file",),
        )


def _request(
    task_id: str = "task-1",
    mode: AgentExecutionMode = AgentExecutionMode.FOREGROUND,
    *,
    timeout: float | None = None,
    kind: AgentLaunchKind = AgentLaunchKind.DEFINED,
) -> AgentLaunchRequest:
    return AgentLaunchRequest(
        task_id=task_id,
        kind=kind,
        task="执行",
        parent_session_id="parent",
        execution_mode=mode,
        timeout_seconds=timeout,
    )


async def test_run_foreground_records_completed_result() -> None:
    manager = AgentTaskManager(ControlledRunner())

    result = await manager.run(_request())

    assert isinstance(result, AgentTaskResult)
    assert result.status is AgentTaskStatus.COMPLETED
    snapshot = manager.get_snapshot("task-1")
    assert snapshot.status is AgentTaskStatus.COMPLETED
    assert snapshot.rounds == 1
    assert snapshot.tool_call_count == 1
    assert manager.drain_notifications("parent") == ()
    manager.close()


async def test_background_task_completes_and_notifies_parent() -> None:
    manager = AgentTaskManager(ControlledRunner(delay=0.01))

    snapshot = await manager.run(_request(mode=AgentExecutionMode.BACKGROUND))
    assert snapshot.status is AgentTaskStatus.BACKGROUND
    await asyncio.sleep(0.05)

    notifications = manager.drain_notifications("parent")
    assert len(notifications) == 1
    assert notifications[0].result.status is AgentTaskStatus.COMPLETED
    manager.close()


async def test_failed_task_is_isolated_as_failed_result() -> None:
    manager = AgentTaskManager(ControlledRunner(fail=True))

    result = await manager.run(_request())

    assert isinstance(result, AgentTaskResult)
    assert result.status is AgentTaskStatus.FAILED
    assert result.error == "boom"
    manager.close()


async def test_cancel_background_task_records_cancelled_notification() -> None:
    manager = AgentTaskManager(ControlledRunner(delay=1))
    await manager.run(_request(mode=AgentExecutionMode.BACKGROUND))

    snapshot = manager.cancel("task-1")

    assert snapshot.status is AgentTaskStatus.CANCELLED
    assert manager.drain_notifications("parent")[0].result.status is AgentTaskStatus.CANCELLED
    manager.close()


async def test_timeout_marks_task_timed_out() -> None:
    manager = AgentTaskManager(ControlledRunner(delay=1))

    result = await manager.run(_request(timeout=0.01))

    assert isinstance(result, AgentTaskResult)
    assert result.status is AgentTaskStatus.TIMED_OUT
    manager.close()


async def test_auto_mode_moves_to_background_after_threshold() -> None:
    manager = AgentTaskManager(ControlledRunner(delay=0.1), auto_background_after=0.01)

    snapshot = await manager.run(_request(mode=AgentExecutionMode.AUTO))

    assert snapshot.status is AgentTaskStatus.BACKGROUND
    same = manager.move_to_background("task-1")
    assert same.task_id == "task-1"
    await asyncio.sleep(0.2)
    assert manager.drain_notifications("parent")[0].result.status is AgentTaskStatus.COMPLETED
    manager.close()


async def test_fork_is_forced_to_background_by_manager() -> None:
    manager = AgentTaskManager(ControlledRunner(delay=0.01))

    snapshot = await manager.run(_request(kind=AgentLaunchKind.FORK))

    assert snapshot.status is AgentTaskStatus.BACKGROUND
    manager.close()
