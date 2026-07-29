from __future__ import annotations

from okcode.prompt import (
    PromptBuildContext,
    PromptBuilder,
    PromptOptionalSections,
    SystemInstruction,
    TurnKind,
)
from okcode.tools.models import ToolDefinition, ToolSafety


def _tools(*, description: str = "读取文件") -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            name="read_file",
            description=description,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            timeout_seconds=5,
            safety=ToolSafety.READ_ONLY,
        ),
    )


def _context(
    *,
    date: str = "2026-07-29",
    root: str = "D:/workspace",
    optional: PromptOptionalSections | None = None,
) -> PromptBuildContext:
    return PromptBuildContext(
        workspace_root=root,
        platform="Windows",
        current_date=date,
        available_tool_names=("read_file",),
        turn_kind=TurnKind.NORMAL,
        optional_sections=optional or PromptOptionalSections(),
    )


def test_builder_orders_fixed_environment_and_optional_sections() -> None:
    bundle = PromptBuilder().build(
        _context(
            optional=PromptOptionalSections(
                custom_instructions="自定义规则",
                active_skills="代码阅读",
                long_term_memory="用户在学习 Agent",
            )
        ),
        _tools(),
    )

    expected = (
        "## 身份",
        "## 系统约束",
        "## 任务模式",
        "## 动作执行",
        "## 工具使用",
        "## 语气风格",
        "## 文本输出",
        "## 环境信息",
        "## 自定义指令",
        "## 已激活的 Skill",
        "## 长期记忆",
    )
    indexes = [bundle.debug_full_prompt.index(section) for section in expected]

    assert indexes == sorted(indexes)
    assert "\n\n## 系统约束" in bundle.debug_full_prompt
    assert "## 环境信息" not in bundle.stable_system
    assert bundle.dynamic_system[0].kind == "environment"


def test_empty_optional_sections_do_not_render_empty_headings() -> None:
    bundle = PromptBuilder().build(_context(), _tools())

    assert "自定义指令" not in bundle.debug_full_prompt
    assert "已激活的 Skill" not in bundle.debug_full_prompt
    assert "长期记忆" not in bundle.debug_full_prompt


def test_dynamic_changes_keep_stable_system_and_cache_key() -> None:
    builder = PromptBuilder()
    first = builder.build(_context(date="2026-07-29", root="D:/one"), _tools())
    second = builder.build(_context(date="2026-07-30", root="D:/two"), _tools())

    assert first.stable_system == second.stable_system
    assert first.cache_key == second.cache_key
    assert first.dynamic_system[0].render() != second.dynamic_system[0].render()


def test_stable_tool_description_changes_cache_key() -> None:
    builder = PromptBuilder()
    first = builder.build(_context(), _tools(description="读取文件"))
    second = builder.build(_context(), _tools(description="读取 UTF-8 文件"))

    assert first.cache_key != second.cache_key


def test_dynamic_instruction_uses_system_note_tag() -> None:
    bundle = PromptBuilder().build(_context(), _tools())

    rendered = bundle.dynamic_system[0].render()
    assert rendered.startswith('<okcode-system-note kind="environment">')
    assert rendered.endswith("</okcode-system-note>")


def test_global_tool_section_repeats_all_critical_rules() -> None:
    bundle = PromptBuilder().build(_context(), _tools())

    for text in ("优先使用专用工具", "编辑前必须先读取", "不要把代码块伪造成工具结果", "保持谨慎"):
        assert text in bundle.stable_system


def test_context_supplements_are_dynamic_ordered_and_not_cached() -> None:
    builder = PromptBuilder()
    baseline = builder.build(_context(), _tools())
    bundle = builder.build(
        PromptBuildContext(
            workspace_root="D:/workspace",
            platform="Windows",
            current_date="2026-07-29",
            available_tool_names=("read_file",),
            additional_system_instructions=(
                SystemInstruction("context_boundary", "重新读取文件", 91),
                SystemInstruction("context_summary", "九段正式摘要", 90),
            ),
        ),
        _tools(),
    )

    assert [instruction.kind for instruction in bundle.dynamic_system] == [
        "environment",
        "context_summary",
        "context_boundary",
    ]
    assert "九段正式摘要" in bundle.debug_full_prompt
    assert "重新读取文件" in bundle.debug_full_prompt
    assert bundle.cache_key == baseline.cache_key
