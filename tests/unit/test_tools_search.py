from __future__ import annotations

from pathlib import Path

import pytest

from okcode.models import ToolCall
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolErrorCode
from okcode.tools.registry import ToolRegistry
from okcode.tools.search import FindFilesTool, SearchCodeTool
from okcode.tools.workspace import Workspace


async def _execute(tool: object, arguments: str):
    registry = ToolRegistry()
    registry.register(tool)  # type: ignore[arg-type]
    return await ToolExecutor(registry).execute(ToolCall("call", tool.definition.name, arguments))  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_find_files_returns_sorted_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "z.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("", encoding="utf-8")
    result = await _execute(FindFilesTool(Workspace(tmp_path)), '{"pattern":"**/*.py"}')

    assert result.success is True
    assert result.data["files"] == ["src/a.py", "src/z.py"]


@pytest.mark.asyncio
async def test_search_code_reports_path_line_and_truncation(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code.py").write_text(
        "first\nneedle here\n" + "needle " * 300,
        encoding="utf-8",
    )
    tool = SearchCodeTool(Workspace(tmp_path))
    result = await _execute(tool, '{"query":"needle","path":"src","pattern":"*.py"}')

    assert result.success is True
    matches = result.data["matches"]
    assert matches[0]["path"] == "src/code.py"  # type: ignore[index]
    assert matches[0]["line_number"] == 2  # type: ignore[index]
    assert result.truncated is True


@pytest.mark.asyncio
async def test_search_rejects_outside_path_and_skips_invalid_encoding(tmp_path: Path) -> None:
    (tmp_path / "valid.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\xffneedle")
    tool = SearchCodeTool(Workspace(tmp_path))

    result = await _execute(tool, '{"query":"needle"}')
    outside = await _execute(tool, '{"query":"needle","path":"../"}')

    assert result.data["matches"] == [{"path": "valid.txt", "line_number": 1, "line": "needle"}]
    assert outside.error_code is ToolErrorCode.OUTSIDE_WORKSPACE


@pytest.mark.asyncio
async def test_find_and_search_reject_external_symlink_as_start_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.py").write_text("needle", encoding="utf-8")
    link = tmp_path / "outside-dir"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接。")

    workspace = Workspace(tmp_path)
    find = await _execute(FindFilesTool(workspace), '{"pattern":"**/*","path":"outside-dir"}')
    search = await _execute(SearchCodeTool(workspace), '{"query":"needle","path":"outside-dir"}')

    assert find.error_code is ToolErrorCode.OUTSIDE_WORKSPACE
    assert search.error_code is ToolErrorCode.OUTSIDE_WORKSPACE
