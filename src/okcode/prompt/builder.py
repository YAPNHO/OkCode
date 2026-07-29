"""请求级系统提示的稳定/动态分层拼装。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from okcode.prompt.cache import build_cache_key
from okcode.prompt.modes import TaskModeInstructionPlanner, TurnKind
from okcode.prompt.sections import environment_content, fixed_sections, optional_sections
from okcode.tools.models import ToolDefinition


@dataclass(frozen=True, slots=True)
class PromptSection:
    """一段可渲染的系统提示模块。"""

    name: str
    priority: int
    content: str
    cacheable: bool

    def render(self) -> str:
        """以稳定标题和正文渲染模块。"""

        return f"## {self.name}\n{self.content}"


@dataclass(frozen=True, slots=True)
class SystemInstruction:
    """不会进入普通会话历史的系统级补充消息。"""

    kind: str
    content: str
    priority: int

    def render(self) -> str:
        """为 Provider 渲染显式系统补充标签。"""

        return f'<okcode-system-note kind="{self.kind}">\n{self.content}\n</okcode-system-note>'


@dataclass(frozen=True, slots=True)
class PromptOptionalSections:
    """后续项目指令、Skill 和记忆功能的预留输入。"""

    custom_instructions: str = ""
    active_skills: str = ""
    long_term_memory: str = ""


@dataclass(frozen=True, slots=True)
class PromptBuildContext:
    """构建一轮提示所需的动态运行事实。"""

    workspace_root: str
    platform: str
    current_date: str
    available_tool_names: tuple[str, ...]
    turn_kind: TurnKind = TurnKind.NORMAL
    iteration: int = 1
    optional_sections: PromptOptionalSections = field(default_factory=PromptOptionalSections)
    additional_system_instructions: tuple[SystemInstruction, ...] = ()


@dataclass(frozen=True, slots=True)
class PromptBundle:
    """一次 Provider 调用所需的稳定和动态提示内容。"""

    stable_system: str
    dynamic_system: tuple[SystemInstruction, ...]
    debug_full_prompt: str
    cache_key: str


class PromptBuilder:
    """集中构建稳定系统提示、运行时补充消息和缓存摘要。"""

    def __init__(self, mode_planner: TaskModeInstructionPlanner | None = None) -> None:
        self._mode_planner = mode_planner or TaskModeInstructionPlanner()

    def build(
        self,
        context: PromptBuildContext,
        tools: Sequence[ToolDefinition],
    ) -> PromptBundle:
        """按优先级构建提示包，动态内容不进入稳定缓存前缀。"""

        fixed = tuple(
            PromptSection(section.name, section.priority, section.content, cacheable=True)
            for section in fixed_sections()
        )
        stable_system = "\n\n".join(section.render() for section in fixed)
        environment = SystemInstruction(
            kind="environment",
            content=environment_content(
                workspace_root=context.workspace_root,
                platform=context.platform,
                current_date=context.current_date,
                available_tool_names=context.available_tool_names,
            ),
            priority=80,
        )
        dynamic: list[SystemInstruction] = [environment]
        optional = optional_sections(
            custom_instructions=context.optional_sections.custom_instructions,
            active_skills=context.optional_sections.active_skills,
            long_term_memory=context.optional_sections.long_term_memory,
        )
        dynamic.extend(
            SystemInstruction(
                kind=_section_kind(section.name), content=section.content, priority=section.priority
            )
            for section in optional
        )
        dynamic.extend(context.additional_system_instructions)
        mode_instruction = self._mode_planner.build(context.turn_kind, context.iteration)
        if mode_instruction is not None:
            dynamic.append(SystemInstruction("task_mode", mode_instruction, priority=120))

        ordered_dynamic = tuple(sorted(dynamic, key=lambda instruction: instruction.priority))
        debug_sections = [
            *fixed,
            PromptSection("环境信息", 80, environment.content, cacheable=False),
        ]
        debug_sections.extend(
            PromptSection(section.name, section.priority, section.content, cacheable=False)
            for section in optional
        )
        debug_sections.extend(
            PromptSection(
                instruction.kind, instruction.priority, instruction.content, cacheable=False
            )
            for instruction in context.additional_system_instructions
        )
        return PromptBundle(
            stable_system=stable_system,
            dynamic_system=ordered_dynamic,
            debug_full_prompt="\n\n".join(section.render() for section in debug_sections),
            cache_key=build_cache_key(stable_system, tools),
        )


def _section_kind(name: str) -> str:
    return {
        "自定义指令": "custom",
        "已激活的 Skill": "skill",
        "长期记忆": "memory",
    }[name]
