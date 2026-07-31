"""Skill 系统在应用装配层使用的轻量门面。"""

from __future__ import annotations

from dataclasses import dataclass, field

from okcode.commands.models import (
    CommandContext,
    CommandDefinition,
    CommandHandler,
    CommandKind,
    CommandResult,
    ForwardedUserMessage,
    ParsedCommand,
    ToolScope,
)
from okcode.commands.registry import CommandRegistry
from okcode.skills.activation import SkillActivationStore
from okcode.skills.catalog import SkillCatalog
from okcode.skills.discovery import SkillDiscoveryResult
from okcode.skills.models import SkillMetadata, SkillValidationError


@dataclass(slots=True)
class SkillRuntime:
    """聚合目录和会话激活快照，并提供提示词使用的动态文本。"""

    catalog: SkillCatalog
    activation_store: SkillActivationStore
    command_registry: CommandRegistry | None = None
    _base_commands: tuple[CommandDefinition, ...] = field(init=False, repr=False)
    _base_registry: CommandRegistry | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._base_commands = (
            self.command_registry.definitions() if self.command_registry is not None else ()
        )
        self._base_registry = (
            CommandRegistry(self._base_commands) if self.command_registry is not None else None
        )

    def refresh(self) -> None:
        """原子刷新可加载定义和动态命令，不修改已激活快照。"""

        result = self.catalog.prepare_refresh()
        if self.command_registry is None:
            self.catalog.commit_refresh(result)
            return
        commands = self.build_skill_commands(result)
        self._validate_command_conflicts(result)
        try:
            self.command_registry.replace((*self._base_commands, *commands))
        except ValueError as exc:
            raise SkillValidationError(f"Skill 命令注册失败：{exc}") from exc
        self.catalog.commit_refresh(result)

    def build_skill_commands(self, result: SkillDiscoveryResult) -> tuple[CommandDefinition, ...]:
        """为最终有效 Skill 构造命令元数据，不读取 SOP 正文。"""

        return tuple(self._command_from_skill(skill) for skill in result.effective)

    def _validate_command_conflicts(self, result: SkillDiscoveryResult) -> None:
        assert self._base_registry is not None
        for skill in result.effective:
            existing = self._base_registry.resolve(skill.name)
            if existing is not None:
                raise SkillValidationError(
                    f"Skill 命令 /{skill.name} 与内置命令 /{existing.name} 冲突："
                    f"Skill 来源 {skill.source_path}。"
                )

    @staticmethod
    def _command_from_skill(skill: SkillMetadata) -> CommandDefinition:
        return CommandDefinition(
            name=skill.name,
            aliases=(),
            description=f"使用 Skill（{skill.source.value}）：{skill.description}",
            usage=f"/{skill.name} [任务]",
            kind=CommandKind.PROMPT,
            argument_hint="任务",
            hidden=False,
            handler=_build_skill_command_handler(skill.name),
        )

    def render_available_section(self) -> str:
        """启动期和普通轮次只公开名称及一句话说明。"""

        skills = self.catalog.list()
        if not skills:
            return ""
        return "\n".join(
            [
                "可按需调用 load_skill 激活以下 Skill；激活前不要假设其完整 SOP。",
                *(f"- {item.name}：{item.description}" for item in skills),
            ]
        )

    def render_active_section(self) -> str:
        """返回固定于当前会话的完整激活 SOP 快照。"""

        return self.activation_store.render_active_section()


def _build_skill_command_handler(skill_name: str) -> CommandHandler:
    """构造转发到指定 Skill 的动态斜杠命令处理函数。"""

    def handler(context: CommandContext, command: ParsedCommand) -> CommandResult:
        task = command.args.strip()
        if task:
            content = (
                f"请使用名为 {skill_name!r} 的 Skill 完成以下任务：{task}\n"
                "请先调用系统级 load_skill 加载该 Skill 的完整 SOP，再严格按 SOP 执行。"
            )
        else:
            content = (
                f"请使用名为 {skill_name!r} 的 Skill，基于当前工作区和该 Skill 的 SOP 执行。\n"
                "请先调用系统级 load_skill 加载该 Skill 的完整 SOP，再严格按 SOP 执行。"
            )
        return CommandResult(
            forward=ForwardedUserMessage(
                content,
                context.conversation.runtime_mode,
                ToolScope.CURRENT_MODE,
                skill_name,
            )
        )

    return handler
