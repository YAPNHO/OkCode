"""worktree 环境初始化。"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from okcode.worktrees.models import WorktreeInitializationReport, WorktreeLease


@dataclass(frozen=True, slots=True)
class WorktreeInitializationRules:
    """可复制或复用的本地运行文件规则。"""

    copy_files: tuple[str, ...] = (
        "config.yaml",
        ".okcode/config.yaml",
        ".okcode/permissions.yaml",
        ".okcode/permissions.local.yaml",
        ".okcode/mcp.yaml",
        ".okcode/hooks.yaml",
    )
    link_directories: tuple[str, ...] = (".venv", ".venv312", "node_modules")
    runtime_files: tuple[str, ...] = ()


class WorktreeInitializer:
    """幂等初始化 worktree 的本地环境。"""

    def __init__(
        self, rules: WorktreeInitializationRules | None = None, *, enable_links: bool = True
    ) -> None:
        self._rules = rules or WorktreeInitializationRules()
        self._enable_links = enable_links

    def initialize(self, lease: WorktreeLease) -> WorktreeInitializationReport:
        source = lease.metadata.repo_root
        target = lease.path
        copied: list[str] = []
        linked: list[str] = []
        warnings: list[str] = []
        for rel in (*self._rules.copy_files, *self._rules.runtime_files):
            result = _copy_if_safe(source / rel, target / rel)
            if result == "copied":
                copied.append(rel)
            elif result:
                warnings.append(result)
        for rel in self._rules.link_directories:
            result = self._link_directory(source / rel, target / rel)
            if result == "linked":
                linked.append(rel)
            elif result:
                warnings.append(result)
        hook_mode = _configure_hooks(source, target, warnings)
        return WorktreeInitializationReport(
            copied_files=tuple(copied),
            linked_directories=tuple(linked),
            hook_mode=hook_mode,
            warnings=tuple(warnings),
        )

    def _link_directory(self, source: Path, target: Path) -> str | None:
        if not self._enable_links or not source.exists() or not source.is_dir():
            return None
        if target.exists() or target.is_symlink():
            try:
                if target.resolve() == source.resolve():
                    return "linked"
            except OSError:
                pass
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.symlink(source, target, target_is_directory=True)
            else:
                target.symlink_to(source, target_is_directory=True)
        except OSError as exc:
            return f"无法链接大型依赖目录 {source.name}：{exc}"
        return "linked"


def _copy_if_safe(source: Path, target: Path) -> str | None:
    if not source.exists() or not source.is_file():
        return None
    if target.exists():
        try:
            if target.read_bytes() == source.read_bytes():
                return None
        except OSError as exc:
            return f"无法比较本地配置 {target}：{exc}"
        return f"目标本地配置已存在且内容不同，已跳过：{target}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return "copied"


def _configure_hooks(source: Path, target: Path, warnings: list[str]) -> str:
    result = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=str(source),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    hooks_path = result.stdout.strip()
    if not hooks_path:
        return "skipped"
    source_hooks = Path(hooks_path)
    if not source_hooks.is_absolute():
        source_hooks = source / source_hooks
    if not source_hooks.exists():
        warnings.append(f"主工作区 hooksPath 不存在，已跳过：{source_hooks}")
        return "skipped"
    config = subprocess.run(
        ["git", "config", "core.hooksPath", str(source_hooks)],
        cwd=str(target),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if config.returncode != 0:
        warnings.append("无法配置 worktree hooksPath，已跳过。")
        return "skipped"
    return "configured"
