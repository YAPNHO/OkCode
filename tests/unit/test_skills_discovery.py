from __future__ import annotations

from pathlib import Path

import pytest

from okcode.skills.catalog import SkillCatalog
from okcode.skills.discovery import SkillRoots, discover_skills
from okcode.skills.models import SkillSourceKind, SkillValidationError


def _write_skill(root: Path, name: str, description: str = "说明") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(
        f"""---
name: {name}
description: {description}
tools: [read_file]
mode: shared
history: recent
model: null
---

执行 {name}。
""",
        encoding="utf-8",
    )
    return path


def _roots(tmp_path: Path) -> SkillRoots:
    return SkillRoots(tmp_path / "builtin", tmp_path / "user", tmp_path / "project")


def test_discovery_uses_project_user_builtin_priority(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _write_skill(roots.builtin, "review", "内置版本")
    _write_skill(roots.user, "review", "用户版本")
    _write_skill(roots.project, "review", "项目版本")

    result = discover_skills(roots)

    assert [(item.name, item.source, item.description) for item in result.effective] == [
        ("review", SkillSourceKind.PROJECT, "项目版本")
    ]
    assert [item.source for item in result.overridden] == [
        SkillSourceKind.BUILTIN,
        SkillSourceKind.USER,
    ]


def test_bad_entry_does_not_block_valid_skill_and_manifest_value_error_is_issue(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    _write_skill(roots.project, "valid")
    bad = roots.project / "bad.md"
    bad.write_text("---\nname: bad\n---\n正文", encoding="utf-8")
    package = roots.project / "package"
    (package / "tools").mkdir(parents=True)
    (package / "SKILL.md").write_text(
        """---
name: package
description: 包
tools: []
mode: shared
history: recent
model: null
---

正文
""",
        encoding="utf-8",
    )
    (package / "tools" / "tools.yaml").write_text(
        "tools:\n  - local_name: echo\n    description: 回显\n"
        "    schema_path: tools/schema.json\n    script_path: tools/run.py\n    safety: invalid\n",
        encoding="utf-8",
    )
    (package / "tools" / "schema.json").write_text("{}", encoding="utf-8")
    (package / "tools" / "run.py").write_text("", encoding="utf-8")

    result = discover_skills(roots)

    assert [item.name for item in result.effective] == ["valid"]
    assert len(result.issues) == 2
    assert any("工具配置无效" in issue.message for issue in result.issues)


def test_same_source_duplicate_and_missing_whitelist_are_startup_errors(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _write_skill(roots.project, "same")
    package = roots.project / "package"
    package.mkdir()
    _write_skill(package, "SKILL", "另一个")
    skill_file = package / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace("name: SKILL", "name: same"),
        encoding="utf-8",
    )

    with pytest.raises(SkillValidationError, match="同名"):
        discover_skills(roots)

    roots = _roots(tmp_path / "missing")
    path = _write_skill(roots.project, "needs-tool")
    path.write_text(
        path.read_text(encoding="utf-8").replace("read_file", "not_found"), encoding="utf-8"
    )
    with pytest.raises(SkillValidationError, match="not_found"):
        SkillCatalog.discover(roots, {"read_file"})


def test_catalog_prepare_refresh_keeps_current_snapshot_until_commit(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _write_skill(roots.project, "first")
    catalog = SkillCatalog.discover(roots, {"read_file"})
    _write_skill(roots.project, "second")

    candidate = catalog.prepare_refresh()

    assert [item.name for item in catalog.list()] == ["first"]
    assert [item.name for item in candidate.effective] == ["first", "second"]

    catalog.commit_refresh(candidate)

    assert [item.name for item in catalog.list()] == ["first", "second"]

    broken = roots.project / "second.md"
    broken.write_text(
        broken.read_text(encoding="utf-8").replace("read_file", "missing_tool"),
        encoding="utf-8",
    )
    with pytest.raises(SkillValidationError, match="missing_tool"):
        catalog.prepare_refresh()

    assert [item.name for item in catalog.list()] == ["first", "second"]
