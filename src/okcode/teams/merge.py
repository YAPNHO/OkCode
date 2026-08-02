"""多成员代码合并。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from okcode.teams.models import TeamMergeReport, TeamMergeRequest, TeamMergeStatus


class TeamMergeManager:
    """基于 Git 的保守顺序合并管理器。"""

    def merge(self, request: TeamMergeRequest) -> TeamMergeReport:
        target = request.target_workspace
        if not _is_clean(target):
            return TeamMergeReport(TeamMergeStatus.FAILED, message="目标工作区不干净，拒绝合并。")
        original = _git(target, "rev-parse", "HEAD")
        merged: list[str] = []
        conflicts: list[str] = []
        for member in request.member_names:
            proc = _git_proc(target, "merge", "--no-edit", member)
            if proc.returncode == 0:
                merged.append(member)
                continue
            conflicts = _conflict_files(target)
            _git_proc(target, "merge", "--abort")
            _git_proc(target, "reset", "--hard", original)
            return TeamMergeReport(
                TeamMergeStatus.ROLLED_BACK,
                merged_members=tuple(merged),
                conflict_files=tuple(conflicts),
                rollback_performed=True,
                message="合并冲突无法安全自动处理，已回滚。",
                source_refs=tuple(request.member_names),
            )
        return TeamMergeReport(
            TeamMergeStatus.CLEAN,
            merged_members=tuple(merged),
            message="成员变更已干净合并。",
            source_refs=tuple(request.member_names),
        )


def _is_clean(path: Path) -> bool:
    return _git(path, "status", "--porcelain") == ""


def _conflict_files(path: Path) -> list[str]:
    output = _git(path, "diff", "--name-only", "--diff-filter=U")
    return [line for line in output.splitlines() if line.strip()]


def _git(path: Path, *args: str) -> str:
    proc = _git_proc(path, *args)
    return proc.stdout.strip()


def _git_proc(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
