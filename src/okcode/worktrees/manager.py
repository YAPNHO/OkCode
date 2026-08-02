"""受管理 Git worktree 生命周期编排。"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from okcode.errors import ConfigError
from okcode.worktrees.git import GitWorktreeClient
from okcode.worktrees.initializer import WorktreeInitializer
from okcode.worktrees.metadata import (
    METADATA_VERSION,
    read_metadata,
    validate_metadata,
    write_metadata,
)
from okcode.worktrees.models import (
    GitStatusSummary,
    WorktreeCleanupPolicy,
    WorktreeCleanupReport,
    WorktreeCleanupStatus,
    WorktreeExitReport,
    WorktreeInitializationReport,
    WorktreeLease,
    WorktreeMetadata,
    WorktreePrepareRequest,
    WorktreeProtectionReason,
)
from okcode.worktrees.naming import validate_worktree_name


class WorktreeManager:
    """创建、恢复、退出和清理受管理 worktree。"""

    def __init__(
        self,
        repo_root: Path,
        *,
        managed_root: Path | None = None,
        git: GitWorktreeClient | None = None,
        initializer: WorktreeInitializer | None = None,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._managed_root = (managed_root or self._repo_root / ".okcode" / "worktrees").resolve()
        self._git = git or GitWorktreeClient()
        self._initializer = initializer or WorktreeInitializer()

    @property
    def managed_root(self) -> Path:
        return self._managed_root

    def prepare(self, request: WorktreePrepareRequest) -> WorktreeLease:
        name = validate_worktree_name(request.identity.name)
        path = (self._managed_root / Path(*name.split("/"))).resolve()
        self._ensure_managed(path)
        common_dir = self._git.repo_common_dir(self._repo_root)
        now = _now()
        expires_at = (
            now + timedelta(seconds=request.ttl_seconds)
            if request.ttl_seconds is not None
            else None
        )
        if path.exists():
            metadata = read_metadata(path)
            validate_metadata(
                metadata,
                repo_root=self._repo_root,
                repo_common_dir=common_dir,
                managed_root=self._managed_root,
                worktree_path=path,
                identity=request.identity,
            )
            metadata = replace(metadata, last_used_at=now, expires_at=expires_at)
            lease = self._lease(path, metadata, created=False, recovered=True)
            report = self._initializer.initialize(lease)
            metadata = replace(metadata, initialization=report)
            write_metadata(metadata)
            return self._lease(path, metadata, created=False, recovered=True, report=report)

        base_head = self._git.resolve_head(self._repo_root, request.base_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git.create_worktree(self._repo_root, path, request.identity.branch, request.base_ref)
        metadata = WorktreeMetadata(
            version=METADATA_VERSION,
            repo_root=self._repo_root,
            repo_common_dir=common_dir,
            managed_root=self._managed_root,
            worktree_path=path,
            identity=request.identity,
            base_ref=request.base_ref,
            base_head=base_head,
            created_at=now,
            last_used_at=now,
            expires_at=expires_at,
        )
        write_metadata(metadata)
        lease = self._lease(path, metadata, created=True, recovered=False)
        report = self._initializer.initialize(lease)
        metadata = replace(metadata, initialization=report)
        write_metadata(metadata)
        return self._lease(path, metadata, created=True, recovered=False, report=report)

    def finalize(self, lease: WorktreeLease, *, force_keep: bool = False) -> WorktreeExitReport:
        report = self.inspect_path(lease.path)
        if force_keep or report.protection_reasons:
            return replace(
                report,
                cleanup_decision=WorktreeCleanupStatus.KEPT,
                cleanup_message=_keep_message(report.protection_reasons),
            )
        try:
            self._git.remove_worktree(self._repo_root, lease.path)
            if lease.path.exists():
                shutil.rmtree(lease.path, ignore_errors=True)
        except ConfigError as exc:
            return replace(
                report,
                cleanup_decision=WorktreeCleanupStatus.FAILED,
                cleanup_message=f"worktree 删除失败，已保留：{exc}",
            )
        return replace(
            report,
            cleanup_decision=WorktreeCleanupStatus.REMOVED,
            cleanup_message="worktree 无变更，已自动删除。",
        )

    def delete(self, name: str, *, force: bool = False) -> WorktreeExitReport:
        safe_name = validate_worktree_name(name)
        path = (self._managed_root / Path(*safe_name.split("/"))).resolve()
        report = self.inspect_path(path)
        reasons = tuple(
            reason
            for reason in report.protection_reasons
            if force
            and reason
            in {
                WorktreeProtectionReason.UNCOMMITTED_CHANGES,
                WorktreeProtectionReason.UNTRACKED_FILES,
                WorktreeProtectionReason.UNPUSHED_COMMITS,
                WorktreeProtectionReason.UNKNOWN_UPSTREAM,
            }
        )
        if report.protection_reasons and not force:
            return replace(
                report,
                cleanup_decision=WorktreeCleanupStatus.KEPT,
                cleanup_message=_keep_message(report.protection_reasons),
            )
        if force and reasons and len(reasons) != len(report.protection_reasons):
            return replace(
                report,
                cleanup_decision=WorktreeCleanupStatus.KEPT,
                cleanup_message=_keep_message(report.protection_reasons),
            )
        try:
            self._git.remove_worktree(self._repo_root, path, force=force)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        except ConfigError as exc:
            return replace(
                report,
                cleanup_decision=WorktreeCleanupStatus.FAILED,
                cleanup_message=f"worktree 删除失败，已保留：{exc}",
            )
        return replace(
            report,
            cleanup_decision=WorktreeCleanupStatus.REMOVED,
            cleanup_message="worktree 已删除。",
        )

    def inspect(self, name: str) -> WorktreeExitReport:
        safe_name = validate_worktree_name(name)
        return self.inspect_path((self._managed_root / Path(*safe_name.split("/"))).resolve())

    def inspect_path(self, path: Path) -> WorktreeExitReport:
        path = path.resolve()
        reasons: list[WorktreeProtectionReason] = []
        try:
            self._ensure_managed(path)
        except ConfigError:
            reasons.append(WorktreeProtectionReason.OUTSIDE_MANAGED_ROOT)
        try:
            metadata = read_metadata(path)
            validate_metadata(
                metadata,
                repo_root=self._repo_root,
                repo_common_dir=self._git.repo_common_dir(self._repo_root),
                managed_root=self._managed_root,
                worktree_path=path,
                identity=metadata.identity,
            )
        except ConfigError:
            metadata = None
            reasons.append(WorktreeProtectionReason.METADATA_MISMATCH)
        status = self._git.status_porcelain(path) if path.exists() else GitStatusSummary()
        status = _without_managed_metadata(status)
        if status.failed:
            reasons.append(WorktreeProtectionReason.GIT_STATUS_FAILED)
        if status.has_uncommitted_changes:
            reasons.append(WorktreeProtectionReason.UNCOMMITTED_CHANGES)
        if status.has_untracked_files:
            reasons.append(WorktreeProtectionReason.UNTRACKED_FILES)
        if metadata is not None:
            try:
                ahead = self._git.ahead_count(path, metadata.base_head)
                if ahead > 0:
                    if self._git.has_upstream(path):
                        reasons.append(WorktreeProtectionReason.UNPUSHED_COMMITS)
                    else:
                        reasons.append(WorktreeProtectionReason.UNKNOWN_UPSTREAM)
            except ConfigError:
                reasons.append(WorktreeProtectionReason.GIT_STATUS_FAILED)
            branch = metadata.identity.branch
            name = metadata.identity.name
        else:
            branch = ""
            name = path.name
        changed = tuple(status.staged + status.modified + status.deleted + status.untracked)
        return WorktreeExitReport(
            path=path,
            branch=branch,
            name=name,
            status_summary=status,
            changed_files=changed[:50],
            protection_reasons=tuple(dict.fromkeys(reasons)),
            cleanup_decision=WorktreeCleanupStatus.KEPT,
            cleanup_message="worktree 已保留。",
        )

    def cleanup_expired(
        self, policy: WorktreeCleanupPolicy | None = None
    ) -> tuple[WorktreeCleanupReport, ...]:
        policy = policy or WorktreeCleanupPolicy()
        if not self._managed_root.exists():
            return ()
        reports: list[WorktreeCleanupReport] = []
        entries = {entry.path.resolve() for entry in self._git.list_worktrees(self._repo_root)}
        for candidate in sorted(self._managed_root.rglob("*")):
            if len(reports) >= policy.max_candidates:
                break
            if not candidate.is_dir():
                continue
            metadata_present = (candidate / ".okcode" / "worktree.json").exists()
            if not metadata_present:
                continue
            managed = _is_relative_to(candidate.resolve(), self._managed_root)
            git_match = candidate.resolve() in entries
            expired = False
            decision = WorktreeCleanupStatus.SKIPPED
            reason = "未过期或不满足清理条件。"
            if managed and metadata_present:
                try:
                    metadata = read_metadata(candidate)
                    expired = metadata.expires_at is not None and metadata.expires_at <= _now()
                except ConfigError as exc:
                    reason = str(exc)
            if managed and metadata_present and git_match and expired:
                report = self.delete(read_metadata(candidate).identity.name)
                decision = report.cleanup_decision
                reason = report.cleanup_message
            reports.append(
                WorktreeCleanupReport(
                    candidate_path=candidate.resolve(),
                    managed_path=managed,
                    metadata_present=metadata_present,
                    git_worktree_match=git_match,
                    expired=expired,
                    decision=decision,
                    reason=reason,
                )
            )
        return tuple(reports)

    def _lease(
        self,
        path: Path,
        metadata: WorktreeMetadata,
        *,
        created: bool,
        recovered: bool,
        report: WorktreeInitializationReport | None = None,
    ) -> WorktreeLease:
        report = report or metadata.initialization
        note = (
            "子 Agent 正在隔离 Git worktree 中运行。\n"
            f"- 主工作区：{metadata.repo_root}\n"
            f"- 隔离工作区：{path}\n"
            f"- 分支：{metadata.identity.branch}\n"
            "- 所有文件和命令工具都应以隔离工作区为边界，不要直接修改主工作区。\n"
            "- 任务结束后无变更会自动清理；有变更或无法确认状态时会保留路径。"
        )
        return WorktreeLease(
            path=path,
            branch=metadata.identity.branch,
            metadata=metadata,
            created=created,
            recovered=recovered,
            initialization_report=report,
            prompt_note=note,
        )

    def _ensure_managed(self, path: Path) -> None:
        if not _is_relative_to(path.resolve(), self._managed_root):
            raise ConfigError("worktree 路径不在受管理目录内。")


def _keep_message(reasons: tuple[WorktreeProtectionReason, ...]) -> str:
    if not reasons:
        return "worktree 已保留。"
    reason_text = ", ".join(reason.value for reason in reasons)
    return f"worktree 有需要保留的状态，未删除：{reason_text}"


def _without_managed_metadata(status: GitStatusSummary) -> GitStatusSummary:
    metadata_names = {".okcode/worktree.json", ".okcode\\worktree.json"}

    def keep(value: str) -> bool:
        return value not in metadata_names

    return GitStatusSummary(
        staged=tuple(item for item in status.staged if keep(item)),
        modified=tuple(item for item in status.modified if keep(item)),
        deleted=tuple(item for item in status.deleted if keep(item)),
        untracked=tuple(item for item in status.untracked if keep(item)),
        raw=tuple(item for item in status.raw if not any(name in item for name in metadata_names)),
        failed=status.failed,
        error=status.error,
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _now() -> datetime:
    return datetime.now(UTC)
