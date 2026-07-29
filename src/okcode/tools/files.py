"""工作区内的文本文件工具。"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from okcode.tools.models import JSONValue, ToolDefinition, ToolErrorCode, ToolFailure, ToolOutput
from okcode.tools.workspace import Workspace

_READ_LIMIT = 12_000


def _object_schema(properties: dict[str, JSONValue], required: list[str]) -> dict[str, JSONValue]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class ReadFileTool:
    """读取工作区内的 UTF-8 文本。"""

    def __init__(self, workspace: Workspace, *, timeout_seconds: float = 10) -> None:
        self._workspace = workspace
        self._definition = ToolDefinition(
            name="read_file",
            description="读取工作区内 UTF-8 文本文件的内容。",
            input_schema=_object_schema({"path": {"type": "string", "minLength": 1}}, ["path"]),
            timeout_seconds=timeout_seconds,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        path = self._workspace.resolve_path(str(arguments["path"]), must_exist=True)
        if path.is_dir():
            raise ToolFailure(ToolErrorCode.IO_ERROR, "目标路径是目录，不能作为文本文件读取。")
        try:
            with path.open("r", encoding="utf-8") as file:
                content = file.read(_READ_LIMIT + 1)
        except UnicodeDecodeError as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "文件不是可读取的 UTF-8 文本。") from exc
        except OSError as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "读取文件失败。") from exc
        truncated = len(content) > _READ_LIMIT
        if truncated:
            content = content[:_READ_LIMIT]
        relative = self._workspace.relative_path(path)
        return ToolOutput(
            content=f"文件 {relative} 的内容：\n{content}",
            data={"path": relative, "content": content},
            truncated=truncated,
        )


class WriteFileTool:
    """原子写入工作区内的 UTF-8 文本。"""

    def __init__(self, workspace: Workspace, *, timeout_seconds: float = 10) -> None:
        self._workspace = workspace
        self._definition = ToolDefinition(
            name="write_file",
            description="在工作区内创建或完整写入 UTF-8 文本文件。",
            input_schema=_object_schema(
                {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                ["path", "content"],
            ),
            timeout_seconds=timeout_seconds,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        path = self._workspace.resolve_path(str(arguments["path"]), must_exist=False)
        content = str(arguments["content"])
        created_parents = _missing_parent_count(path.parent, self._workspace.root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, content)
        except OSError as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "写入文件失败。") from exc
        relative = self._workspace.relative_path(path)
        return ToolOutput(
            content=f"已写入文件 {relative}。",
            data={
                "path": relative,
                "characters_written": len(content),
                "created_parent_count": created_parents,
            },
        )


class EditFileTool:
    """只允许唯一原文匹配的安全替换。"""

    def __init__(self, workspace: Workspace, *, timeout_seconds: float = 10) -> None:
        self._workspace = workspace
        self._definition = ToolDefinition(
            name="edit_file",
            description="在工作区文本文件中将唯一匹配的原文替换为新文本。",
            input_schema=_object_schema(
                {
                    "path": {"type": "string", "minLength": 1},
                    "old_text": {"type": "string", "minLength": 1},
                    "new_text": {"type": "string"},
                },
                ["path", "old_text", "new_text"],
            ),
            timeout_seconds=timeout_seconds,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        path = self._workspace.resolve_path(str(arguments["path"]), must_exist=True)
        if path.is_dir():
            raise ToolFailure(ToolErrorCode.IO_ERROR, "目标路径是目录，不能编辑。")
        old_text = str(arguments["old_text"])
        new_text = str(arguments["new_text"])
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "文件不是可编辑的 UTF-8 文本。") from exc
        except OSError as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "读取待编辑文件失败。") from exc

        match_count = original.count(old_text)
        relative = self._workspace.relative_path(path)
        if match_count == 0:
            raise ToolFailure(
                ToolErrorCode.MATCH_NOT_FOUND,
                "原文未在目标文件中找到，文件未修改。",
                {"path": relative, "match_count": match_count},
            )
        if match_count != 1:
            raise ToolFailure(
                ToolErrorCode.MATCH_NOT_UNIQUE,
                f"原文在目标文件中出现了 {match_count} 次，无法安全替换，文件未修改。",
                {"path": relative, "match_count": match_count},
            )

        try:
            _atomic_write(path, original.replace(old_text, new_text, 1))
        except OSError as exc:
            raise ToolFailure(ToolErrorCode.IO_ERROR, "替换文件内容失败，原文件保持不变。") from exc
        return ToolOutput(
            content=f"已在 {relative} 中完成一次唯一替换。",
            data={"path": relative, "replacements": 1},
        )


def _missing_parent_count(parent: Path, root: Path) -> int:
    count = 0
    current = parent
    while current != root and not current.exists():
        count += 1
        current = current.parent
    return count


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".okcode-", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
