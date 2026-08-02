from __future__ import annotations

from pathlib import Path

from okcode.teams.backends import BackendSelector
from okcode.teams.mailbox import MailboxStore
from okcode.teams.models import (
    BackendPreference,
    TeamBackendKind,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageProtocol,
    TeamTaskStatus,
)
from okcode.teams.runtime import TeamRuntime
from okcode.teams.store import TeamStore
from tests.unit.test_teams_backends import FakeBackend


def _runtime(tmp_path: Path) -> TeamRuntime:
    return TeamRuntime(
        store=TeamStore(tmp_path, lock_timeout_seconds=0.2, stale_lock_seconds=0.2),
        mailbox=MailboxStore(lock_timeout_seconds=0.2, stale_lock_seconds=0.2),
        selector=BackendSelector((TeamBackendKind.COROUTINE,)),
        backends=(FakeBackend(TeamBackendKind.COROUTINE),),
    )


def test_runtime_create_team_adds_lead_mailbox_registry(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    snapshot = runtime.create_team("core", "session-1")
    lead = runtime.store.read_registry("core").get("lead")

    assert snapshot.metadata.name == "core"
    assert lead is not None
    assert lead.mailbox_path.exists()
    assert runtime.use_team("core", "session-2").metadata.leader_session_id == "session-2"


def test_runtime_add_member_tasks_and_status_updates(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.create_team("core", "session-1")
    member = runtime.add_member(
        "core",
        name="worker",
        role="builder",
        workdir=tmp_path,
        backend_preference=BackendPreference(required_kind=TeamBackendKind.COROUTINE),
    )
    task = runtime.create_task(
        "core",
        title="build",
        body="do it",
        owner="worker",
        dependencies=("task-0",),
    )
    updated = runtime.update_task("core", task.task_id, status=TeamTaskStatus.RUNNING.value)

    assert member.status is TeamMemberStatus.IDLE
    assert updated.status is TeamTaskStatus.RUNNING
    assert runtime.snapshot("core").tasks[0].dependencies == ("task-0",)


def test_runtime_message_send_broadcast_and_approval(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.create_team("core", "session-1")
    runtime.add_member("core", name="worker", role="builder", workdir=tmp_path)

    delivered = runtime.send_message("core", "lead", "worker", TeamMessage("", "", "hello"))
    missing = runtime.send_message("core", "lead", "missing", TeamMessage("", "", "hello"))
    broadcast = runtime.broadcast("core", "lead", TeamMessage("", "", "all"))
    approval = runtime.create_approval_request("core", "worker", "task-1", "plan")

    assert delivered.status == "delivered"
    assert missing.status == "failed"
    assert [item.recipient for item in broadcast.results] == ["worker"]
    assert approval.protocol is TeamMessageProtocol.APPROVAL_REQUEST
    assert runtime.snapshot("core").members[0].status is TeamMemberStatus.WAITING_APPROVAL


def test_runtime_restore_reports_missing_context_and_workdir(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.create_team("core", "session-1")
    runtime.add_member("core", name="worker", role="builder", workdir=tmp_path)

    report = runtime.restore_member("core", "worker")

    assert report.status == "failed"
    assert "上下文" in report.error
    assert runtime.snapshot("core").members[0].status is TeamMemberStatus.UNRECOVERABLE
