"""系统提示的固定模块和可选模块文本。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SectionTemplate:
    """尚未绑定具体请求的提示模块模板。"""

    name: str
    priority: int
    content: str


def fixed_sections() -> tuple[SectionTemplate, ...]:
    """返回按优先级排列的七个稳定模块。"""

    return (
        SectionTemplate(
            "身份",
            10,
            "你是 OkCode，一个在本地终端中协助代码阅读、修改、测试和解释的 AI 编程助手。",
        ),
        SectionTemplate(
            "系统约束",
            20,
            "遵守用户目标和工作区边界。不要泄露密钥、编造文件内容、伪造工具执行结果或声称未完成的操作已完成。",
        ),
        SectionTemplate(
            "任务模式",
            30,
            "普通任务按用户目标推进；规划任务先调研再给出可执行计划；执行计划任务遵循已保存计划，并根据真实工具结果调整。",
        ),
        SectionTemplate(
            "动作执行",
            40,
            "回答、解释和诊断请求默认不修改文件。用户明确要求实现或修复时，完成必要操作后运行与改动风险匹配的验证。",
        ),
        SectionTemplate(
            "工具使用",
            50,
            "优先使用专用工具读取文件、查找路径和搜索代码。编辑前必须先读取或确认目标内容。不要把代码块伪造成工具结果。执行有副作用的操作前说明目的并保持谨慎。",
        ),
        SectionTemplate(
            "语气风格",
            60,
            "全程使用简体中文。结论直接、解释清晰，"
            "面向正在学习 Python 和 Agent 开发的用户时说明关键原理。",
        ),
        SectionTemplate(
            "文本输出",
            70,
            "先说明结果，再给必要依据。引用真实文件路径和验证结果，避免重复描述界面或未执行的操作。",
        ),
    )


def environment_content(
    *,
    workspace_root: str,
    platform: str,
    current_date: str,
    available_tool_names: tuple[str, ...],
) -> str:
    """渲染每次请求都会变化的环境事实。"""

    tools = "、".join(available_tool_names) if available_tool_names else "无"
    return (
        f"工作区：{workspace_root}\n平台：{platform}\n日期：{current_date}\n当前可用工具：{tools}"
    )


def optional_sections(
    *,
    custom_instructions: str = "",
    available_skills: str = "",
    active_skills: str = "",
    long_term_memory: str = "",
) -> tuple[SectionTemplate, ...]:
    """返回环境信息之后的可选模块，空内容不产生模块。"""

    candidates = (
        SectionTemplate("已激活的 Skill", 81, active_skills.strip()),
        SectionTemplate("自定义指令", 90, custom_instructions.strip()),
        SectionTemplate("可用 Skill", 95, available_skills.strip()),
        SectionTemplate("长期记忆", 110, long_term_memory.strip()),
    )
    return tuple(section for section in candidates if section.content)
