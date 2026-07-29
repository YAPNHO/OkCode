"""工作区路径边界。"""

from __future__ import annotations

from pathlib import Path

from okcode.tools.models import ToolErrorCode, ToolFailure


class Workspace:
    """把文件工具限制在启动时确定的根目录内。"""

    def __init__(self, root: Path) -> None:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("工作区根目录必须是目录。")
        self._root = resolved

    @property
    def root(self) -> Path:
        return self._root

    def resolve_path(self, raw_path: str, *, must_exist: bool) -> Path:
        """解析单个文件路径，并拒绝所有离开工作区的形式。"""

        return self.resolve_path_with_relative(raw_path, must_exist=must_exist)[0]

    def resolve_path_with_relative(self, raw_path: str, *, must_exist: bool) -> tuple[Path, str]:
        """返回已解析的工作区路径及其稳定的项目相对显示形式。"""

        if not raw_path or raw_path.isspace():
            self._outside_workspace()
        candidate = Path(raw_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            self._outside_workspace()
        target = self._root / candidate
        try:
            resolved = target.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise ToolFailure(ToolErrorCode.NOT_FOUND, "目标文件或目录不存在。") from exc
        except (OSError, RuntimeError) as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "无法解析工作区中的目标路径。") from exc
        self._ensure_within(resolved)
        return resolved, resolved.relative_to(self._root).as_posix() or "."

    def resolve_directory(self, raw_path: str | None) -> Path:
        target = self._root if raw_path is None else self.resolve_path(raw_path, must_exist=True)
        if not target.is_dir():
            raise ToolFailure(ToolErrorCode.INVALID_ARGUMENTS, "path 必须是工作区内的目录。")
        return target

    def ensure_candidate(self, candidate: Path) -> Path:
        """验证遍历得到的候选项，跳过时调用方可捕获越界失败。"""

        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "无法解析工作区中的候选路径。") from exc
        self._ensure_within(resolved)
        return resolved

    def relative_path(self, path: Path) -> str:
        resolved = self.ensure_candidate(path)
        return resolved.relative_to(self._root).as_posix() or "."

    def _ensure_within(self, path: Path) -> None:
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise ToolFailure(
                ToolErrorCode.OUTSIDE_WORKSPACE,
                "路径必须位于当前工作区内。",
            ) from exc

    @staticmethod
    def _outside_workspace() -> None:
        raise ToolFailure(
            ToolErrorCode.OUTSIDE_WORKSPACE,
            "路径必须是当前工作区内、不包含父级回退的相对路径。",
        )
