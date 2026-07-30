"""分层项目指令加载与安全的 ``@include`` 展开。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from okcode.errors import ConfigError

_INCLUDE_PATTERN = re.compile(r"^\s*@include\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class InstructionPaths:
    """三层手写 ``AGENTS.md`` 的固定位置。"""

    root: Path
    project: Path
    user: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> InstructionPaths:
        """返回当前项目和当前用户的三层指令位置。"""

        return cls(
            root=workspace_root / "AGENTS.md",
            project=workspace_root / ".okcode" / "AGENTS.md",
            user=Path.home() / ".okcode" / "AGENTS.md",
        )


class InstructionLoader:
    """按固定优先级加载指令，并限制引用在工作区内。"""

    def __init__(
        self,
        paths: InstructionPaths,
        workspace_root: Path,
        *,
        max_include_depth: int = 5,
    ) -> None:
        if max_include_depth < 1:
            raise ValueError("指令引用最大深度必须为正数。")
        self._paths = paths
        self._workspace_root = workspace_root.resolve(strict=True)
        self._max_include_depth = max_include_depth

    def load(self) -> str:
        """按项目根、项目目录、用户目录顺序合并所有存在的指令。"""

        sections = []
        for path in (self._paths.root, self._paths.project, self._paths.user):
            if not path.is_file():
                continue
            content = self._expand(path.resolve(strict=True), depth=0, visited=set())
            if content.strip():
                sections.append(content.strip())
        return "\n\n".join(sections)

    def _expand(self, path: Path, *, depth: int, visited: set[Path]) -> str:
        if depth > self._max_include_depth:
            raise ConfigError(f"项目指令引用超过最大深度：{path}")
        if path in visited:
            raise ConfigError(f"项目指令存在循环引用：{path}")
        visited.add(path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ConfigError(f"无法读取项目指令：{path}") from exc

        expanded: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            match = _INCLUDE_PATTERN.fullmatch(line)
            if match is None:
                expanded.append(line)
                continue
            target = self._include_target(match.group(1), path, line_number)
            expanded.append(self._expand(target, depth=depth + 1, visited=visited))
        visited.remove(path)
        return "\n".join(expanded)

    def _include_target(self, raw_target: str, source: Path, line_number: int) -> Path:
        target_text = raw_target.strip()
        candidate = Path(target_text)
        if not target_text or candidate.is_absolute() or ".." in candidate.parts:
            raise ConfigError(f"{source}:{line_number} 的 @include 必须是项目内相对路径。")
        try:
            resolved = (self._workspace_root / candidate).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ConfigError(f"{source}:{line_number} 引用的文件不存在：{target_text}") from exc
        except OSError as exc:
            raise ConfigError(f"{source}:{line_number} 无法解析引用：{target_text}") from exc
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ConfigError(f"{source}:{line_number} 的 @include 超出项目目录。") from exc
        if not resolved.is_file():
            raise ConfigError(f"{source}:{line_number} 的 @include 必须引用文件：{target_text}")
        return resolved
