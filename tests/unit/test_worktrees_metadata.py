from datetime import UTC, datetime
from pathlib import Path

import pytest

from okcode.errors import ConfigError
from okcode.worktrees.metadata import read_metadata, validate_metadata, write_metadata
from okcode.worktrees.models import (
    WorktreeIdentity,
    WorktreeInitializationReport,
    WorktreeMetadata,
)


def _metadata(root: Path, worktree: Path) -> WorktreeMetadata:
    now = datetime.now(UTC)
    return WorktreeMetadata(
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
        initialization=WorktreeInitializationReport(copied_files=("config.yaml",)),
    )


def test_metadata_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    worktree = root / ".okcode" / "worktrees" / "agents" / "reviewer" / "task"
    worktree.mkdir(parents=True)
    metadata = _metadata(root, worktree)

    write_metadata(metadata)
    loaded = read_metadata(worktree)

    assert loaded.identity == metadata.identity
    assert loaded.repo_root == root
    assert loaded.initialization.copied_files == ("config.yaml",)


def test_validate_metadata_rejects_path_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    worktree = root / ".okcode" / "worktrees" / "agents" / "reviewer" / "task"
    worktree.mkdir(parents=True)
    metadata = _metadata(root, worktree)

    with pytest.raises(ConfigError):
        validate_metadata(
            metadata,
            repo_root=root / "other",
            repo_common_dir=root / ".git",
            managed_root=root / ".okcode" / "worktrees",
            worktree_path=worktree,
            identity=metadata.identity,
        )


def test_read_metadata_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        read_metadata(tmp_path)
