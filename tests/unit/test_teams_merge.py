from __future__ import annotations

import subprocess
from pathlib import Path

from okcode.teams.merge import TeamMergeManager
from okcode.teams.models import TeamMergeRequest, TeamMergeStatus


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ("git", *args),
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "OkCode Test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    return repo


def test_merge_manager_merges_clean_member_branches(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-b", "worker-a")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _commit(repo, "worker a")
    _git(repo, "checkout", "master")
    _git(repo, "checkout", "-b", "worker-b")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _commit(repo, "worker b")
    _git(repo, "checkout", "master")

    report = TeamMergeManager().merge(TeamMergeRequest("core", ("worker-a", "worker-b"), repo))

    assert report.status is TeamMergeStatus.CLEAN
    assert report.merged_members == ("worker-a", "worker-b")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert (repo / "b.txt").read_text(encoding="utf-8") == "b\n"


def test_merge_manager_rolls_back_unresolvable_conflict(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    _commit(repo, "shared base")
    original = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "worker-a")
    (repo / "shared.txt").write_text("a\n", encoding="utf-8")
    _commit(repo, "worker a")
    _git(repo, "checkout", "master")
    _git(repo, "checkout", "-b", "worker-b")
    (repo / "shared.txt").write_text("b\n", encoding="utf-8")
    _commit(repo, "worker b")
    _git(repo, "checkout", "master")

    report = TeamMergeManager().merge(TeamMergeRequest("core", ("worker-a", "worker-b"), repo))

    assert report.status is TeamMergeStatus.ROLLED_BACK
    assert report.rollback_performed is True
    assert report.conflict_files == ("shared.txt",)
    assert _git(repo, "rev-parse", "HEAD") == original
    assert _git(repo, "status", "--porcelain") == ""


def test_merge_manager_rejects_dirty_target_workspace(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    report = TeamMergeManager().merge(TeamMergeRequest("core", ("worker-a",), repo))

    assert report.status is TeamMergeStatus.FAILED
    assert "不干净" in report.message
