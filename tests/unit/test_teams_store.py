from __future__ import annotations

from pathlib import Path

from okcode.teams.models import (
    TeamBackendKind,
    TeamMember,
    TeamMemberStatus,
    TeamMetadata,
    TeamTask,
    TeamTaskStatus,
)
from okcode.teams.store import TeamStore


def _store(tmp_path: Path) -> TeamStore:
    return TeamStore(tmp_path, lock_timeout_seconds=0.2, stale_lock_seconds=0.2)


def test_store_creates_expected_files_and_reloadable_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.create(
        TeamMetadata(
            version=1,
            name="core",
            leader_session_id="session-1",
            root_path=tmp_path / "core",
        )
    )

    paths = store.paths("core")
    assert snapshot.metadata.name == "core"
    assert paths.team_json.exists()
    assert paths.members_json.exists()
    assert paths.tasks_json.exists()
    assert paths.registry_json.exists()
    assert paths.mailboxes_dir.is_dir()
    assert paths.member_sessions_dir.is_dir()
    assert _store(tmp_path).load("core").metadata.leader_session_id == "session-1"


def test_store_upserts_member_and_registry_with_managed_mailbox(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(TeamMetadata(1, "core", "session-1", tmp_path / "core"))
    member = TeamMember(
        name="worker",
        role="builder",
        workdir=tmp_path,
        backend=TeamBackendKind.COROUTINE,
        mailbox_path=Path("ignored.jsonl"),
    )

    stored = store.upsert_member("core", member)
    registry = store.read_registry("core")

    assert stored.mailbox_path == store.paths("core").mailbox_path("worker")
    assert stored.mailbox_path.exists()
    assert registry.get("worker") is not None
    assert registry.get("worker").mailbox_path == stored.mailbox_path  # type: ignore[union-attr]


def test_store_updates_member_status_without_losing_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(TeamMetadata(1, "core", "session-1", tmp_path / "core"))
    store.upsert_member(
        "core",
        TeamMember("worker", "builder", tmp_path, TeamBackendKind.COROUTINE, tmp_path),
    )

    updated = store.update_member_status(
        "core",
        "worker",
        TeamMemberStatus.BLOCKED,
        error="blocked",
    )

    assert updated.role == "builder"
    assert updated.status is TeamMemberStatus.BLOCKED
    assert updated.last_error == "blocked"
    assert store.read_registry("core").get("worker").status is TeamMemberStatus.BLOCKED  # type: ignore[union-attr]


def test_store_mutates_tasks_atomically_and_preserves_dependencies(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(TeamMetadata(1, "core", "session-1", tmp_path / "core"))
    task = TeamTask("task-1", "title", "body", dependencies=("task-0",))

    store.mutate_tasks("core", lambda tasks: [*tasks, task])
    store.mutate_tasks(
        "core",
        lambda tasks: [
            task if task.task_id != "task-1" else task.__class__(
                task.task_id,
                task.title,
                task.body,
                task.owner,
                TeamTaskStatus.BLOCKED,
                task.dependencies,
                "waiting",
            )
            for task in tasks
        ],
    )

    [stored] = store.list_tasks("core")
    assert stored.status is TeamTaskStatus.BLOCKED
    assert stored.dependencies == ("task-0",)
    assert stored.blocked_reason == "waiting"
