from __future__ import annotations

from pathlib import Path

import pytest

from okcode.models import ToolCall
from okcode.tools.executor import ToolExecutor
from okcode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from okcode.tools.models import ToolErrorCode
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace


async def _execute(tool: object, arguments: str):
    registry = ToolRegistry()
    registry.register(tool)  # type: ignore[arg-type]
    return await ToolExecutor(registry).execute(ToolCall("call", tool.definition.name, arguments))  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_read_file_reads_and_truncates_utf8_text(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("你好", encoding="utf-8")
    result = await _execute(ReadFileTool(Workspace(tmp_path)), '{"path":"note.txt"}')

    assert result.success is True
    assert result.data["content"] == "你好"

    (tmp_path / "large.txt").write_text("x" * 13_000, encoding="utf-8")
    large = await _execute(ReadFileTool(Workspace(tmp_path)), '{"path":"large.txt"}')
    assert large.truncated is True


@pytest.mark.asyncio
async def test_read_file_reports_missing_directory_invalid_encoding_and_outside(
    tmp_path: Path,
) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "binary.bin").write_bytes(b"\xff")
    workspace = Workspace(tmp_path)
    tool = ReadFileTool(workspace)

    missing = await _execute(tool, '{"path":"missing.txt"}')
    directory = await _execute(tool, '{"path":"folder"}')
    binary = await _execute(tool, '{"path":"binary.bin"}')
    outside = await _execute(tool, '{"path":"../outside.txt"}')

    assert missing.error_code is ToolErrorCode.NOT_FOUND
    assert directory.error_code is ToolErrorCode.IO_ERROR
    assert binary.error_code is ToolErrorCode.IO_ERROR
    assert outside.error_code is ToolErrorCode.OUTSIDE_WORKSPACE


@pytest.mark.asyncio
async def test_write_file_creates_parent_and_replaces_content(tmp_path: Path) -> None:
    tool = WriteFileTool(Workspace(tmp_path))
    created = await _execute(tool, '{"path":"nested/note.txt","content":"first"}')
    overwritten = await _execute(tool, '{"path":"nested/note.txt","content":"second"}')

    assert created.success is True
    assert created.data["created_parent_count"] == 1
    assert overwritten.success is True
    assert (tmp_path / "nested" / "note.txt").read_text(encoding="utf-8") == "second"


@pytest.mark.asyncio
async def test_edit_file_requires_exactly_one_match_without_mutating_failures(
    tmp_path: Path,
) -> None:
    path = tmp_path / "note.txt"
    path.write_text("before\nneedle\nafter", encoding="utf-8")
    tool = EditFileTool(Workspace(tmp_path))

    success = await _execute(
        tool,
        '{"path":"note.txt","old_text":"needle","new_text":"replacement"}',
    )
    assert success.success is True
    assert path.read_text(encoding="utf-8") == "before\nreplacement\nafter"

    unchanged = path.read_bytes()
    missing = await _execute(
        tool,
        '{"path":"note.txt","old_text":"missing","new_text":"x"}',
    )
    assert missing.error_code is ToolErrorCode.MATCH_NOT_FOUND
    assert path.read_bytes() == unchanged

    path.write_text("same same", encoding="utf-8")
    unchanged = path.read_bytes()
    multiple = await _execute(
        tool,
        '{"path":"note.txt","old_text":"same","new_text":"x"}',
    )
    assert multiple.error_code is ToolErrorCode.MATCH_NOT_UNIQUE
    assert path.read_bytes() == unchanged


@pytest.mark.asyncio
async def test_file_tools_reject_external_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("外部内容", encoding="utf-8")
    link = tmp_path / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接。")

    workspace = Workspace(tmp_path)
    read = await _execute(ReadFileTool(workspace), '{"path":"outside-link.txt"}')
    write = await _execute(
        WriteFileTool(workspace),
        '{"path":"outside-link.txt","content":"不允许"}',
    )
    edit = await _execute(
        EditFileTool(workspace),
        '{"path":"outside-link.txt","old_text":"外部","new_text":"内部"}',
    )

    assert [read.error_code, write.error_code, edit.error_code] == [
        ToolErrorCode.OUTSIDE_WORKSPACE,
        ToolErrorCode.OUTSIDE_WORKSPACE,
        ToolErrorCode.OUTSIDE_WORKSPACE,
    ]
    assert outside.read_text(encoding="utf-8") == "外部内容"
