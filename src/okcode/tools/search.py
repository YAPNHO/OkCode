"""工作区内的文件查找与文本搜索工具。"""

from __future__ import annotations

from collections.abc import Mapping

from okcode.tools.files import _object_schema
from okcode.tools.models import JSONValue, ToolDefinition, ToolErrorCode, ToolFailure, ToolOutput
from okcode.tools.workspace import Workspace

_MAX_FILES = 200
_MAX_MATCHES = 200
_MAX_LINE_LENGTH = 500


class FindFilesTool:
    """按 glob 模式返回工作区中的文件。"""

    def __init__(self, workspace: Workspace, *, timeout_seconds: float = 10) -> None:
        self._workspace = workspace
        self._definition = ToolDefinition(
            name="find_files",
            description="按 glob 模式查找工作区内的文件，例如 **/*.py。",
            input_schema=_object_schema(
                {
                    "pattern": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "minLength": 1},
                },
                ["pattern"],
            ),
            timeout_seconds=timeout_seconds,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        base = self._workspace.resolve_directory(_optional_path(arguments))
        pattern = str(arguments["pattern"])
        files: list[str] = []
        truncated = False
        try:
            candidates = base.glob(pattern)
            for candidate in candidates:
                try:
                    resolved = self._workspace.ensure_candidate(candidate)
                except ToolFailure:
                    continue
                if not resolved.is_file():
                    continue
                if len(files) >= _MAX_FILES:
                    truncated = True
                    break
                files.append(self._workspace.relative_path(resolved))
        except (OSError, ValueError) as exc:
            raise ToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                "文件模式无效或无法遍历工作区。",
            ) from exc

        files.sort()
        return ToolOutput(
            content=f"找到 {len(files)} 个匹配文件。",
            data={"files": files},
            truncated=truncated,
        )


class SearchCodeTool:
    """逐行搜索工作区中的 UTF-8 文本文件。"""

    def __init__(self, workspace: Workspace, *, timeout_seconds: float = 10) -> None:
        self._workspace = workspace
        self._definition = ToolDefinition(
            name="search_code",
            description="在工作区 UTF-8 文本文件中搜索文本，返回路径、行号和匹配行。",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "minLength": 1},
                    "pattern": {"type": "string", "minLength": 1},
                },
                ["query"],
            ),
            timeout_seconds=timeout_seconds,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        base = self._workspace.resolve_directory(_optional_path(arguments))
        pattern = str(arguments.get("pattern", "**/*"))
        query = str(arguments["query"])
        matches: list[dict[str, JSONValue]] = []
        truncated = False
        try:
            candidates = base.glob(pattern)
            for candidate in candidates:
                try:
                    resolved = self._workspace.ensure_candidate(candidate)
                except ToolFailure:
                    continue
                if not resolved.is_file():
                    continue
                try:
                    with resolved.open("r", encoding="utf-8") as file:
                        for line_number, line in enumerate(file, start=1):
                            if query not in line:
                                continue
                            if len(matches) >= _MAX_MATCHES:
                                truncated = True
                                break
                            display_line = line.rstrip("\r\n")
                            if len(display_line) > _MAX_LINE_LENGTH:
                                display_line = display_line[:_MAX_LINE_LENGTH] + "[行已截断]"
                                truncated = True
                            matches.append(
                                {
                                    "path": self._workspace.relative_path(resolved),
                                    "line_number": line_number,
                                    "line": display_line,
                                }
                            )
                except UnicodeDecodeError:
                    continue
                except OSError:
                    continue
                if truncated and len(matches) >= _MAX_MATCHES:
                    break
        except (OSError, ValueError) as exc:
            raise ToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                "搜索模式无效或无法遍历工作区。",
            ) from exc

        return ToolOutput(
            content=f"找到 {len(matches)} 条匹配。",
            data={"matches": matches},
            truncated=truncated,
        )


def _optional_path(arguments: Mapping[str, JSONValue]) -> str | None:
    value = arguments.get("path")
    return value if isinstance(value, str) else None
