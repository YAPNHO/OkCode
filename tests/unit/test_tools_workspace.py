from __future__ import annotations

from pathlib import Path

import pytest

from okcode.tools.models import ToolErrorCode, ToolFailure
from okcode.tools.workspace import Workspace


def test_workspace_accepts_nested_relative_paths_and_rejects_escapes(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    nested = workspace.resolve_path("nested/new.txt", must_exist=False)

    assert nested == tmp_path / "nested" / "new.txt"
    for raw_path in ("../secret.txt", str(tmp_path.parent / "secret.txt"), ""):
        with pytest.raises(ToolFailure) as raised:
            workspace.resolve_path(raw_path, must_exist=False)
        assert raised.value.code is ToolErrorCode.OUTSIDE_WORKSPACE


def test_workspace_rejects_missing_and_non_directory_search_root(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ToolFailure) as missing:
        workspace.resolve_path("missing.txt", must_exist=True)
    with pytest.raises(ToolFailure) as not_directory:
        workspace.resolve_directory("file.txt")

    assert missing.value.code is ToolErrorCode.NOT_FOUND
    assert not_directory.value.code is ToolErrorCode.INVALID_ARGUMENTS


def test_workspace_rejects_symlink_to_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("外部内容", encoding="utf-8")
    link = tmp_path / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接。")

    with pytest.raises(ToolFailure) as raised:
        Workspace(tmp_path).resolve_path("outside-link.txt", must_exist=True)
    assert raised.value.code is ToolErrorCode.OUTSIDE_WORKSPACE


def test_workspace_normalizes_windows_style_relative_paths(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)

    resolved, relative = workspace.resolve_path_with_relative("nested\\new.txt", must_exist=False)

    assert resolved == tmp_path / "nested" / "new.txt"
    assert relative == "nested/new.txt"
