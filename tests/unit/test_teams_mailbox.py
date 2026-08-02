from __future__ import annotations

from pathlib import Path

import pytest

from okcode.teams.locking import FileLock, LockAcquireError
from okcode.teams.mailbox import MailboxFormatError, MailboxStore
from okcode.teams.models import TeamMessage


def _mailbox(tmp_path: Path) -> Path:
    return tmp_path / "mailboxes" / "worker.jsonl"


def test_mailbox_append_unread_and_mark_read(tmp_path: Path) -> None:
    store = MailboxStore(lock_timeout_seconds=0.2, stale_lock_seconds=0.2)
    path = _mailbox(tmp_path)

    stored = store.append(path, TeamMessage("lead", "worker", "hello"))
    unread = store.unread(path)
    marked = store.mark_read(path, (stored.message_id,))

    assert stored.message_id.startswith("msg-")
    assert stored.created_at is not None
    assert stored.read is False
    assert stored.summary == "hello"
    assert unread == (stored,)
    assert marked[0].read is True
    assert store.unread(path) == ()


def test_mailbox_bad_json_reports_diagnostic(tmp_path: Path) -> None:
    path = _mailbox(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{bad json}\n", encoding="utf-8")

    with pytest.raises(MailboxFormatError, match="无法解析"):
        MailboxStore().unread(path)


def test_mailbox_append_many_keeps_successes_when_one_target_fails(tmp_path: Path) -> None:
    store = MailboxStore(lock_timeout_seconds=0.2, stale_lock_seconds=0.2)
    good = _mailbox(tmp_path)
    bad_parent = tmp_path / "not-a-dir"
    bad_parent.write_text("file", encoding="utf-8")
    bad = bad_parent / "bad.jsonl"

    report = store.append_many(
        (good, bad),
        lambda path: TeamMessage("lead", path.stem, "hello"),
    )

    assert [item.status for item in report.results] == ["delivered", "failed"]
    assert store.unread(good)[0].recipient == "worker"


def test_file_lock_timeout_and_stale_takeover(tmp_path: Path) -> None:
    lock_path = tmp_path / "box.lock"
    first = FileLock.acquire(lock_path, timeout_seconds=0.2, stale_seconds=30, owner="one")
    try:
        with pytest.raises(LockAcquireError):
            FileLock.acquire(lock_path, timeout_seconds=0.01, stale_seconds=30, owner="two")
    finally:
        FileLock.release(first)

    stale = FileLock.acquire(lock_path, timeout_seconds=0.2, stale_seconds=0.01, owner="old")
    try:
        assert lock_path.exists()
    finally:
        FileLock.release(stale)
    takeover = FileLock.acquire(lock_path, timeout_seconds=0.2, stale_seconds=0.01, owner="new")
    FileLock.release(takeover)
