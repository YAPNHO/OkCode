from __future__ import annotations

from pathlib import Path

import pytest

from okcode.errors import ConfigError
from okcode.instructions import InstructionLoader, InstructionPaths


def _paths(tmp_path: Path) -> InstructionPaths:
    return InstructionPaths(
        root=tmp_path / "AGENTS.md",
        project=tmp_path / ".okcode" / "AGENTS.md",
        user=tmp_path / "user" / ".okcode" / "AGENTS.md",
    )


def _loader(tmp_path: Path, *, max_include_depth: int = 5) -> InstructionLoader:
    return InstructionLoader(_paths(tmp_path), tmp_path, max_include_depth=max_include_depth)


def test_loads_three_layers_in_descending_priority_order(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.root.write_text("根指令", encoding="utf-8")
    paths.project.parent.mkdir()
    paths.project.write_text("项目指令", encoding="utf-8")
    paths.user.parent.mkdir(parents=True)
    paths.user.write_text("用户指令", encoding="utf-8")

    assert _loader(tmp_path).load() == "根指令\n\n项目指令\n\n用户指令"


def test_load_expands_workspace_relative_include(tmp_path: Path) -> None:
    (tmp_path / "shared.md").write_text("被引用内容", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("根\n@include shared.md\n尾", encoding="utf-8")

    assert _loader(tmp_path).load() == "根\n被引用内容\n尾"


def test_load_ignores_missing_top_level_files(tmp_path: Path) -> None:
    assert _loader(tmp_path).load() == ""


def test_load_rejects_include_cycle(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("@include a.md", encoding="utf-8")
    (tmp_path / "a.md").write_text("@include AGENTS.md", encoding="utf-8")

    with pytest.raises(ConfigError, match="循环"):
        _loader(tmp_path).load()


def test_load_rejects_include_over_maximum_depth(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("@include a.md", encoding="utf-8")
    (tmp_path / "a.md").write_text("@include b.md", encoding="utf-8")
    (tmp_path / "b.md").write_text("内容", encoding="utf-8")

    with pytest.raises(ConfigError, match="最大深度"):
        _loader(tmp_path, max_include_depth=1).load()


@pytest.mark.parametrize("include", ("../outside.md", "C:/outside.md"))
def test_load_rejects_outside_include_path(tmp_path: Path, include: str) -> None:
    (tmp_path / "AGENTS.md").write_text(f"@include {include}", encoding="utf-8")

    with pytest.raises(ConfigError, match="项目内相对路径"):
        _loader(tmp_path).load()


def test_load_rejects_symbolic_link_that_escapes_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("外部内容", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")
    (tmp_path / "AGENTS.md").write_text("@include link.md", encoding="utf-8")

    with pytest.raises(ConfigError, match="超出项目目录"):
        _loader(tmp_path).load()
