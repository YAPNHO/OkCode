"""Git worktree 隔离的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class WorktreeProtectionReason(StrEnum):
    """阻止删除 worktree 的原因。"""

    UNCOMMITTED_CHANGES = "uncommitted_changes"
    UNTRACKED_FILES = "untracked_files"
    UNPUSHED_COMMITS = "unpushed_commits"
    UNKNOWN_UPSTREAM = "unknown_upstream"
    METADATA_MISMATCH = "metadata_mismatch"
    OUTSIDE_MANAGED_ROOT = "outside_managed_root"
    GIT_STATUS_FAILED = "git_status_failed"


class WorktreeCleanupStatus(StrEnum):
    """worktree 清理决策。"""

    REMOVED = "removed"
    KEPT = "kept"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class WorktreeIdentity:
    """一个受管理 worktree 的稳定身份。"""

    name: str
    branch: str
    task_id: str
    parent_session_id: str
    role_name: str | None
    trigger: str


@dataclass(frozen=True, slots=True)
class WorktreeInitializationReport:
    """worktree 环境初始化结果。"""

    copied_files: tuple[str, ...] = ()
    linked_directories: tuple[str, ...] = ()
    hook_mode: str = "skipped"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorktreeMetadata:
    """写入 worktree 内部的持久元数据。"""

    version: int
    repo_root: Path
    repo_common_dir: Path
    managed_root: Path
    worktree_path: Path
    identity: WorktreeIdentity
    base_ref: str
    base_head: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime | None
    initialization: WorktreeInitializationReport = field(
        default_factory=WorktreeInitializationReport
    )


@dataclass(frozen=True, slots=True)
class WorktreePrepareRequest:
    """创建或恢复 worktree 的请求。"""

    identity: WorktreeIdentity
    main_workspace: Path
    base_ref: str = "HEAD"
    ttl_seconds: int | None = 86_400


@dataclass(frozen=True, slots=True)
class WorktreeLease:
    """子 Agent 运行期间持有的 worktree 租约。"""

    path: Path
    branch: str
    metadata: WorktreeMetadata
    created: bool
    recovered: bool
    initialization_report: WorktreeInitializationReport
    prompt_note: str


@dataclass(frozen=True, slots=True)
class GitStatusSummary:
    """Git 工作区状态摘要。"""

    staged: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    raw: tuple[str, ...] = ()
    failed: bool = False
    error: str | None = None

    @property
    def has_uncommitted_changes(self) -> bool:
        return bool(self.staged or self.modified or self.deleted)

    @property
    def has_untracked_files(self) -> bool:
        return bool(self.untracked)


@dataclass(frozen=True, slots=True)
class GitWorktreeEntry:
    """git worktree list --porcelain 的单个条目。"""

    path: Path
    head: str | None
    branch: str | None


@dataclass(frozen=True, slots=True)
class WorktreeExitReport:
    """worktree 退出或删除后的状态报告。"""

    path: Path
    branch: str
    name: str
    status_summary: GitStatusSummary
    changed_files: tuple[str, ...]
    protection_reasons: tuple[WorktreeProtectionReason, ...]
    cleanup_decision: WorktreeCleanupStatus
    cleanup_message: str


@dataclass(frozen=True, slots=True)
class WorktreeCleanupPolicy:
    """后台清理策略。"""

    max_candidates: int = 50


@dataclass(frozen=True, slots=True)
class WorktreeCleanupReport:
    """后台清理单个候选目录的结果。"""

    candidate_path: Path
    managed_path: bool
    metadata_present: bool
    git_worktree_match: bool
    expired: bool
    decision: WorktreeCleanupStatus
    reason: str
