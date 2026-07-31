"""内置斜杠命令处理函数。"""

from __future__ import annotations

from okcode.commands.models import (
    CommandContext,
    CommandResult,
    CommandUiAction,
    ForwardedUserMessage,
    ParsedCommand,
    RuntimeMode,
    ToolScope,
)
from okcode.models import (
    CommandHelp,
    CommandHelpEntry,
    CommandMemory,
    CommandNotice,
    CommandSession,
    CommandStatus,
    RuntimeModeChanged,
    SkillListEntry,
    SkillListEvent,
)
from okcode.skills.models import SkillError

_REVIEW_PROMPT = (
    "Please review the current git diff for code changes. Focus on:\n"
    "1. Logic errors\n"
    "2. Security issues\n"
    "3. Performance problems\n"
    "4. Code style"
)


def exit_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    return CommandResult(ui_action=CommandUiAction.EXIT)


def plan_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    context.conversation.set_runtime_mode(RuntimeMode.PLAN)
    return CommandResult(events=(RuntimeModeChanged(RuntimeMode.PLAN.value, "已切换到计划模式。"),))


def do_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    context.conversation.set_runtime_mode(RuntimeMode.DEFAULT)
    return CommandResult(
        events=(
            RuntimeModeChanged(RuntimeMode.DEFAULT.value, "已切换到默认模式，开始执行最近计划。"),
        ),
        stream=context.conversation.stream_do_instruction(),
    )


def compact_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    return CommandResult(stream=context.conversation.stream_manual_compaction())


def resume_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    return CommandResult(ui_action=CommandUiAction.SELECT_SESSION)


def clear_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    return CommandResult(ui_action=CommandUiAction.RESET_SESSION)


def help_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    visible = context.registry.visible_commands()
    return CommandResult(
        events=(
            CommandHelp(tuple(CommandHelpEntry(item.name, item.description) for item in visible)),
        )
    )


def status_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    snapshot = context.conversation.status_snapshot()
    return CommandResult(
        events=(
            CommandStatus(
                snapshot.permission_mode,
                snapshot.cumulative_input_tokens,
                snapshot.cumulative_output_tokens,
                snapshot.available_tool_count,
                snapshot.loaded_memory_item_count,
                snapshot.model_name,
                snapshot.working_directory,
            ),
        )
    )


def memory_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    snapshot = context.conversation.memory_snapshot()
    return CommandResult(
        events=(CommandMemory(snapshot.project_memory_files, snapshot.user_memory_files),)
    )


def permission_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    return CommandResult(events=(CommandNotice(context.conversation.permission_string()),))


def session_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    snapshot = context.conversation.session_snapshot()
    return CommandResult(events=(CommandSession(snapshot.session_id, snapshot.journal_path),))


def skill_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    runtime = context.skill_runtime
    if runtime is None:
        return CommandResult(events=(CommandNotice("当前会话未启用 Skill 系统。"),))
    refresh = getattr(runtime, "refresh", None)
    refresh_error = None
    if callable(refresh):
        try:
            refresh()
        except SkillError as exc:
            refresh_error = str(exc)
    catalog = getattr(runtime, "catalog", None)
    activation_store = getattr(runtime, "activation_store", None)
    active_names = set()
    if activation_store is not None:
        active_names = {item.name for item in activation_store.active()}
    entries = []
    issues = ()
    if catalog is not None:
        entries = [
            SkillListEntry(
                item.name,
                item.description,
                item.source.value,
                item.name in active_names,
                item.version_id,
            )
            for item in catalog.list()
        ]
        issues = tuple(issue.render() for issue in catalog.issues_for_display())
    if refresh_error is not None:
        issues = (*issues, f"刷新失败：{refresh_error}")
    return CommandResult(events=(SkillListEvent(tuple(entries), issues),))


def review_command(context: CommandContext, command: ParsedCommand) -> CommandResult:
    return CommandResult(
        forward=ForwardedUserMessage(
            _REVIEW_PROMPT,
            RuntimeMode.DEFAULT,
            ToolScope.CURRENT_MODE,
            "review",
        )
    )
