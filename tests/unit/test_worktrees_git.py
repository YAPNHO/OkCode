from pathlib import Path

from okcode.worktrees.git import GitWorktreeClient


def _git(repo: Path, *args: str) -> None:
    client = GitWorktreeClient()
    client._git(repo, *args)  # type: ignore[attr-defined]


def test_git_worktree_client_creates_lists_status_and_removes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    client = GitWorktreeClient()
    worktree = tmp_path / "wt"

    client.create_worktree(repo, worktree, "okcode/agents/test", "HEAD")
    (worktree / "new.txt").write_text("new\n", encoding="utf-8")

    entries = client.list_worktrees(repo)
    status = client.status_porcelain(worktree)

    assert any(entry.path == worktree.resolve() for entry in entries)
    assert "new.txt" in status.untracked
    assert client.ahead_count(worktree, client.resolve_head(repo)) == 0
    client.remove_worktree(repo, worktree, force=True)
    assert not worktree.exists()
