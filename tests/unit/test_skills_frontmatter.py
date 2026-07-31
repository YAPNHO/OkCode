from __future__ import annotations

from pathlib import Path

import pytest

from okcode.skills.frontmatter import extract_placeholders, parse_skill_markdown, render_body
from okcode.skills.models import SkillArgumentError, SkillParseError


def _write_skill(path: Path, body: str = "执行 {{task}}。") -> None:
    path.write_text(
        """---
name: demo
description: 演示 Skill
tools: [read_file]
mode: shared
history: recent
model: null
---

"""
        + body,
        encoding="utf-8",
    )


def test_frontmatter_parses_metadata_without_retaining_sop_during_scan(tmp_path: Path) -> None:
    path = tmp_path / "demo.md"
    _write_skill(path)

    scanned = parse_skill_markdown(path, include_body=False)
    loaded = parse_skill_markdown(path)

    assert scanned.name == "demo"
    assert scanned.description == "演示 Skill"
    assert scanned.body == ""
    assert loaded.body == "执行 {{task}}。"


@pytest.mark.parametrize(
    "content, message",
    [
        ("name: demo", "YAML frontmatter"),
        ("---\nname: demo\n---\n正文", "description"),
        (
            "---\nname: demo\ndescription: d\ntools: read_file\n"
            "mode: shared\nhistory: recent\n---\n正文",
            "tools",
        ),
        (
            "---\nname: demo\ndescription: d\ntools: []\nmode: bad\nhistory: recent\n---\n正文",
            "mode",
        ),
    ],
)
def test_invalid_frontmatter_reports_diagnostic(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    path = tmp_path / "bad.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(SkillParseError, match=message):
        parse_skill_markdown(path)


def test_render_body_replaces_placeholders_and_keeps_extra_arguments() -> None:
    assert extract_placeholders("处理 {{task}}，级别 {{level}}") == ("level", "task")

    rendered = render_body("处理 {{task}}", {"task": "当前改动", "strict": True})

    assert "处理 当前改动" in rendered
    assert '"strict": true' in rendered
    with pytest.raises(SkillArgumentError, match="task"):
        render_body("处理 {{task}}", {})
    with pytest.raises(SkillArgumentError, match="无效"):
        render_body("处理 {{invalid name}}", {})
