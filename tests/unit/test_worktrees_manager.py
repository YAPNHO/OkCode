from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from okcode.errors import ConfigError
from okcode.worktrees.initializer import WorktreeInitializer
from okcode.worktrees.manager import WorktreeManager
from okcode.worktrees.metadata import read_metadata, write_metadata
from okcode.worktrees.models import (
    GitStatusSummary,
    GitWorktreeEntry,
    WorktreeCleanupStatus,
    WorktreeIdentity,
    WorktreeInitializationReport,
    WorktreePrepareRequest,
    WorktreeProtectionReason,
)


class FakeGit:
    def __init__(self) -> None:
        self.add_calls = 0
        self.remove_calls = 0
        self.status = GitStatusSummary()
        self.ahead = 0
        self.upstream = False
        self.worktrees: set[Path] = set()

    def repo_common_dir(self, cwd: Path) -> Path:
        return cwd / ".git"

    def resolve_head(self, cwd: Path, ref: str = "HEAD") -> str:
        return "base"

    def create_worktree(self, repo_root: Path, path: Path, branch: str, base_ref: str) -> None:
        self.add_calls += 1
        path.mkdir(parents=True)
        self.worktrees.add(path.resolve())

    def remove_worktree(self, repo_root: Path, path: Path, *, force: bool = False) -> None:
        self.remove_calls += 1
        self.worktrees.discard(path.resolve())

    def list_worktrees(self, repo_root: Path) -> tuple[GitWorktreeEntry, ...]:
        return tuple(
            GitWorktreeEntry(path=path, head="base", branch=None) for path in self.worktrees
        )

    def status_porcelain(self, path: Path) -> GitStatusSummary:
        return self.status

    def ahead_count(self, path: Path, base_head: str) -> int:
        return self.ahead

    def has_upstream(self, path: Path) -> bool:
        return self.upstream


class FakeInitializer(WorktreeInitializer):
    def initialize(self, lease):
        return WorktreeInitializationReport(copied_files=("config.yaml",))


def _identity() -> WorktreeIdentity:
    return WorktreeIdentity(
        name="agents/reviewer/task",
        branch="okcode/agents/agents/reviewer/task",
        task_id="task",
        parent_session_id="session",
        role_name="reviewer",
        trigger="tool",
    )


def _manager(tmp_path: Path, git: FakeGit) -> WorktreeManager:
    root = tmp_path / "repo"
    root.mkdir()
    return WorktreeManager(root, git=git, initializer=FakeInitializer())


def test_prepare_creates_worktree_and_metadata(tmp_path: Path) -> None:
    git = FakeGit()
    manager = _manager(tmp_path, git)

    lease = manager.prepare(WorktreePrepareRequest(_identity(), tmp_path / "repo"))

    assert git.add_calls == 1
    assert lease.created is True
    assert lease.path.exists()
    assert read_metadata(lease.path).identity == _identity()


def test_prepare_recovers_existing_metadata_without_git_add(tmp_path: Path) -> None:
    git = FakeGit()
    manager = _manager(tmp_path, git)
    request = WorktreePrepareRequest(_identity(), tmp_path / "repo")
    first = manager.prepare(request)
    git.add_calls = 0

    recovered = manager.prepare(request)

    assert recovered.recovered is True
    assert git.add_calls == 0
    assert recovered.path == first.path


def test_prepare_rejects_existing_directory_without_metadata(tmp_path: Path) -> None:
    git = FakeGit()
    manager = _manager(tmp_path, git)
    path = tmp_path / "repo" / ".okcode" / "worktrees" / "agents" / "reviewer" / "task"
    path.mkdir(parents=True)

    with pytest.raises(ConfigError):
        manager.prepare(WorktreePrepareRequest(_identity(), tmp_path / "repo"))


def test_finalize_removes_clean_worktree(tmp_path: Path) -> None:
    git = FakeGit()
    manager = _manager(tmp_path, git)
    lease = manager.prepare(WorktreePrepareRequest(_identity(), tmp_path / "repo"))

    report = manager.finalize(lease)

    assert report.cleanup_decision is WorktreeCleanupStatus.REMOVED
    assert git.remove_calls == 1


def test_finalize_keeps_uncommitted_worktree(tmp_path: Path) -> None:
    git = FakeGit()
    git.status = GitStatusSummary(modified=("file.py",))
    manager = _manager(tmp_path, git)
    lease = manager.prepare(WorktreePrepareRequest(_identity(), tmp_path / "repo"))

    report = manager.finalize(lease)

    assert report.cleanup_decision is WorktreeCleanupStatus.KEPT
    assert WorktreeProtectionReason.UNCOMMITTED_CHANGES in report.protection_reasons
    assert git.remove_calls == 0


def test_finalize_keeps_changed_head_without_upstream(tmp_path: Path) -> None:
    git = FakeGit()
    git.ahead = 1
    git.upstream = False
    manager = _manager(tmp_path, git)
    lease = manager.prepare(WorktreePrepareRequest(_identity(), tmp_path / "repo"))

    report = manager.finalize(lease)

    assert WorktreeProtectionReason.UNKNOWN_UPSTREAM in report.protection_reasons


def test_cleanup_expired_uses_three_layer_filter(tmp_path: Path) -> None:
    git = FakeGit()
    manager = _manager(tmp_path, git)
    lease = manager.prepare(WorktreePrepareRequest(_identity(), tmp_path / "repo"))
    metadata = read_metadata(lease.path)
    write_metadata(replace(metadata, expires_at=datetime.now(UTC) - timedelta(seconds=1)))

    reports = manager.cleanup_expired()

    assert reports
    assert reports[0].managed_path is True
    assert reports[0].metadata_present is True
    assert reports[0].git_worktree_match is True
