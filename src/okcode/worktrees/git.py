"""Git worktree 命令封装。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from okcode.errors import ConfigError
from okcode.worktrees.models import GitStatusSummary, GitWorktreeEntry


class GitWorktreeClient:
    """所有 Git 调用都显式传入 cwd。"""

    def repo_common_dir(self, cwd: Path) -> Path:
        output = self._git(cwd, "rev-parse", "--git-common-dir")
        common = Path(output.strip())
        if not common.is_absolute():
            common = cwd / common
        return common.resolve()

    def resolve_head(self, cwd: Path, ref: str = "HEAD") -> str:
        return self._git(cwd, "rev-parse", ref).strip()

    def create_worktree(self, repo_root: Path, path: Path, branch: str, base_ref: str) -> None:
        self._git(repo_root, "worktree", "add", "-b", branch, str(path), base_ref)

    def remove_worktree(self, repo_root: Path, path: Path, *, force: bool = False) -> None:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        self._git(repo_root, *args)

    def list_worktrees(self, repo_root: Path) -> tuple[GitWorktreeEntry, ...]:
        output = self._git(repo_root, "worktree", "list", "--porcelain")
        entries: list[GitWorktreeEntry] = []
        current: dict[str, str] = {}
        for line in output.splitlines():
            if not line:
                if current:
                    entries.append(_entry(current))
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        if current:
            entries.append(_entry(current))
        return tuple(entries)

    def status_porcelain(self, path: Path) -> GitStatusSummary:
        try:
            output = self._git(
                path,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
        except ConfigError as exc:
            return GitStatusSummary(failed=True, error=str(exc))
        staged: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        untracked: list[str] = []
        raw: list[str] = []
        for line in output.splitlines():
            if not line:
                continue
            raw.append(line)
            code = line[:2]
            file_name = line[3:] if len(line) > 3 else ""
            if code == "??":
                untracked.append(file_name)
                continue
            if code[0] != " ":
                staged.append(file_name)
            if code[1] in {"M", "A", "R", "C"}:
                modified.append(file_name)
            if code[1] == "D" or code[0] == "D":
                deleted.append(file_name)
        return GitStatusSummary(
            staged=tuple(staged),
            modified=tuple(modified),
            deleted=tuple(deleted),
            untracked=tuple(untracked),
            raw=tuple(raw),
        )

    def ahead_count(self, path: Path, base_head: str) -> int:
        output = self._git(path, "rev-list", "--count", f"{base_head}..HEAD").strip()
        try:
            return int(output)
        except ValueError:
            return 0

    def has_upstream(self, path: Path) -> bool:
        try:
            self._git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        except ConfigError:
            return False
        return True

    def check_ref_format(self, repo_root: Path, branch: str) -> bool:
        try:
            self._git(repo_root, "check-ref-format", "--branch", branch)
        except ConfigError:
            return False
        return True

    def _git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Git 命令失败。").strip()
            raise ConfigError(message)
        return result.stdout


def _entry(data: dict[str, str]) -> GitWorktreeEntry:
    branch = data.get("branch")
    if branch and branch.startswith("refs/heads/"):
        branch = branch.removeprefix("refs/heads/")
    return GitWorktreeEntry(
        path=Path(data["worktree"]).resolve(),
        head=data.get("HEAD"),
        branch=branch,
    )
