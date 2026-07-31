"""SkillCatalog：有效 Skill 查询、热更新和完整定义加载。"""

from __future__ import annotations

from dataclasses import dataclass

from okcode.skills.discovery import (
    SkillDiscoveryResult,
    SkillRoots,
    dedicated_tool_names,
    discover_skills,
)
from okcode.skills.frontmatter import extract_placeholders, parse_skill_markdown
from okcode.skills.models import (
    SkillDefinition,
    SkillMetadata,
    SkillParseIssue,
    SkillValidationError,
    normalize_skill_name,
)

LOAD_SKILL_TOOL_NAME = "load_skill"


@dataclass(slots=True)
class SkillCatalog:
    """当前进程的可加载 Skill 列表。"""

    roots: SkillRoots
    known_tool_names: set[str]
    _result: SkillDiscoveryResult | None = None

    @classmethod
    def discover(cls, roots: SkillRoots, known_tool_names: set[str]) -> SkillCatalog:
        catalog = cls(roots, set(known_tool_names))
        catalog.refresh()
        return catalog

    def refresh(self) -> None:
        """立即发现并提交最新 Skill 目录快照。"""

        self.commit_refresh(self.prepare_refresh())

    def prepare_refresh(self) -> SkillDiscoveryResult:
        """构造并校验候选快照，不修改当前可见目录。"""

        result = discover_skills(self.roots)
        self._validate(result)
        return result

    def commit_refresh(self, result: SkillDiscoveryResult) -> None:
        """提交已通过后续运行时校验的候选快照。"""

        self._result = result

    def list(self) -> tuple[SkillMetadata, ...]:
        return self._require_result().effective

    def get(self, name: str) -> SkillMetadata | None:
        key = normalize_skill_name(name)
        for item in self.list():
            if item.key == key:
                return item
        return None

    def issues_for_display(self) -> tuple[SkillParseIssue, ...]:
        return self._require_result().issues

    def load_definition(self, name: str) -> SkillDefinition:
        metadata = self.get(name)
        if metadata is None:
            key = normalize_skill_name(name)
            issue = next(
                (
                    item
                    for item in self._require_result().issues
                    if normalize_skill_name(item.source_path.stem) == key
                    or normalize_skill_name(item.source_path.name) == key
                ),
                None,
            )
            if issue is not None:
                raise SkillValidationError(f"Skill {name!r} 无法加载：{issue.message}")
            raise SkillValidationError(f"不存在名为 {name!r} 的 Skill。")
        parsed = parse_skill_markdown(metadata.entry_path, include_body=True)
        if normalize_skill_name(parsed.name) != metadata.key:
            raise SkillValidationError(f"Skill 名称已变化，请刷新后重试：{metadata.entry_path}")
        placeholders = extract_placeholders(parsed.body)
        return SkillDefinition(
            metadata=metadata,
            body=parsed.body,
            placeholders=placeholders,
            dedicated_tools=metadata.dedicated_tools,
        )

    def _validate(self, result: SkillDiscoveryResult) -> None:
        available = set(self.known_tool_names)
        available.add(LOAD_SKILL_TOOL_NAME)
        dedicated = dedicated_tool_names(result.effective)
        conflicts = dedicated & self.known_tool_names
        if conflicts:
            raise SkillValidationError(
                f"目录型 Skill 专属工具与已有工具重名：{', '.join(sorted(conflicts))}"
            )
        available.update(dedicated)
        for skill in result.effective:
            missing = [tool for tool in skill.allowed_tools if tool not in available]
            if missing:
                raise SkillValidationError(
                    f"Skill {skill.name!r} 引用了不存在的工具：{', '.join(sorted(missing))}"
                )

    def _require_result(self) -> SkillDiscoveryResult:
        if self._result is None:
            self.refresh()
        assert self._result is not None
        return self._result
