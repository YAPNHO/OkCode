from datetime import UTC, datetime
from pathlib import Path

from okcode.worktrees.initializer import (
    WorktreeInitializationRules,
    WorktreeInitializer,
)
from okcode.worktrees.models import (
    WorktreeIdentity,
    WorktreeLease,
    WorktreeMetadata,
)


def _lease(root: Path, worktree: Path) -> WorktreeLease:
    now = datetime.now(UTC)
    metadata = WorktreeMetadata(
        version=1,
        repo_root=root,
        repo_common_dir=root / ".git",
        managed_root=root / ".okcode" / "worktrees",
        worktree_path=worktree,
        identity=WorktreeIdentity(
            name="agents/reviewer/task",
            branch="okcode/agents/agents/reviewer/task",
            task_id="task",
            parent_session_id="session",
            role_name="reviewer",
            trigger="tool",
        ),
        base_ref="HEAD",
        base_head="abc",
        created_at=now,
        last_used_at=now,
        expires_at=None,
    )
    return WorktreeLease(
        path=worktree,
        branch=metadata.identity.branch,
        metadata=metadata,
        created=True,
        recovered=False,
        initialization_report=metadata.initialization,
        prompt_note="",
    )


def test_initializer_copies_allowlisted_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    worktree = tmp_path / "wt"
    root.mkdir()
    worktree.mkdir()
    (root / "config.yaml").write_text("model: test\n", encoding="utf-8")
    (root / ".env").write_text("secret\n", encoding="utf-8")

    report = WorktreeInitializer(
        WorktreeInitializationRules(copy_files=("config.yaml",), link_directories=()),
        enable_links=False,
    ).initialize(_lease(root, worktree))

    assert (worktree / "config.yaml").read_text(encoding="utf-8") == "model: test\n"
    assert not (worktree / ".env").exists()
    assert report.copied_files == ("config.yaml",)


def test_initializer_does_not_overwrite_modified_target(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    worktree = tmp_path / "wt"
    root.mkdir()
    worktree.mkdir()
    (root / "config.yaml").write_text("source\n", encoding="utf-8")
    (worktree / "config.yaml").write_text("target\n", encoding="utf-8")

    report = WorktreeInitializer(
        WorktreeInitializationRules(copy_files=("config.yaml",), link_directories=()),
        enable_links=False,
    ).initialize(_lease(root, worktree))

    assert (worktree / "config.yaml").read_text(encoding="utf-8") == "target\n"
    assert report.warnings
