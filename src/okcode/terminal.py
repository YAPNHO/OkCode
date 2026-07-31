"""行内终端输入和 Rich 渲染。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from okcode.commands.completion import SlashCommandCompleter
from okcode.commands.models import RuntimeMode
from okcode.commands.registry import CommandRegistry
from okcode.errors import ProviderError
from okcode.mcp.models import McpDiscoveryWarning
from okcode.models import (
    AgentProgress,
    AgentStopped,
    CommandHelp,
    CommandMemory,
    CommandNotice,
    CommandSession,
    CommandStatus,
    HookListEvent,
    PermissionStatus,
    ProviderConfig,
    RuntimeModeChanged,
    SkillListEvent,
    TextDelta,
    ThinkingDelta,
    TokenUsageReported,
    ToolCallRequested,
    ToolExecutionFinished,
    ToolExecutionStarted,
    TurnEvent,
    VisibleDelta,
)
from okcode.permissions.models import PermissionConfirmation, PermissionRequest
from okcode.sessions import SessionDescriptor
from okcode.tools.models import ToolErrorCode


class TerminalUI:
    """保留普通终端滚动历史的最小交互界面。"""

    def __init__(
        self, *, console: Console | None = None, session: PromptSession | None = None
    ) -> None:
        self._console = console or Console()
        self._session = session
        self._seen_sections: set[str] = set()
        self._turn_open = False
        self._command_registry: CommandRegistry | None = None
        self._runtime_mode = RuntimeMode.DEFAULT

    def prompt(self) -> str | None:
        """读取一轮输入；EOF 表示退出。"""

        while True:
            try:
                session = self._session
                if session is None:
                    session = self._new_prompt_session()
                    self._session = session
                return session.prompt("你 > ")
            except KeyboardInterrupt:
                self._console.print()
            except EOFError:
                return None

    def show_welcome(self, config: ProviderConfig, permission_mode: str = "default") -> None:
        self._console.print(
            f"OkCode | {config.name} | {config.protocol} | {config.model} | 权限：{permission_mode}"
        )

    def select_session(self, sessions: Sequence[SessionDescriptor]) -> str | None:
        """展示可恢复会话，并返回用户选中的日志 ID。"""

        if not sessions:
            self._console.print(
                "\u6ca1\u6709\u53ef\u6062\u590d\u7684\u4f1a\u8bdd\u3002", style="dim"
            )
            return None

        self._finish_line()
        table = Table(
            title="\u53ef\u6062\u590d\u4f1a\u8bdd",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("\u7f16\u53f7", justify="right", no_wrap=True)
        table.add_column("\u4f1a\u8bdd ID", no_wrap=True)
        table.add_column("\u6807\u9898")
        table.add_column("\u6d88\u606f\u6570", justify="right", no_wrap=True)
        table.add_column("\u6700\u8fd1\u66f4\u65b0", no_wrap=True)
        for index, session in enumerate(sessions, start=1):
            table.add_row(
                str(index),
                session.id,
                session.title,
                str(session.message_count),
                session.updated_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            )
        self._console.print(table)

        while True:
            try:
                session = self._session
                if session is None:
                    session = self._new_prompt_session()
                    self._session = session
                choice = session.prompt(
                    "\u6062\u590d\u7f16\u53f7\uff08\u56de\u8f66\u6216 /cancel \u53d6\u6d88\uff09> "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                self._console.print("\u5df2\u53d6\u6d88\u6062\u590d\u3002", style="dim")
                return None
            if not choice or choice.lower() == "/cancel":
                self._console.print("\u5df2\u53d6\u6d88\u6062\u590d\u3002", style="dim")
                return None
            try:
                index = int(choice)
            except ValueError:
                self._console.print(
                    "\u8bf7\u8f93\u5165\u4f1a\u8bdd\u7f16\u53f7\uff0c\u6216\u6309\u56de\u8f66\u53d6\u6d88\u3002",
                    style="yellow",
                )
                continue
            if 1 <= index <= len(sessions):
                return sessions[index - 1].id
            self._console.print(
                "\u4f1a\u8bdd\u7f16\u53f7\u4e0d\u5b58\u5728\uff0c\u8bf7\u91cd\u65b0\u8f93\u5165\u3002",
                style="yellow",
            )

    async def confirm_permission(self, request: PermissionRequest) -> PermissionConfirmation:
        """在运行中的事件循环内等待一次明确的用户决定。"""

        target = request.display_target or "无主操作目标"
        self._console.print(f"权限确认：{request.call.name} -> {target}", style="yellow")
        self._console.print("此操作可能修改项目或影响系统，请确认是否允许。", style="yellow")
        self._console.print("选择：d=拒绝，o=仅本次，s=本会话，p=永久允许，/exit=退出", style="dim")
        try:
            session = self._session
            if session is None:
                session = self._new_prompt_session()
                self._session = session
            choice = (await session.prompt_async("权限 > ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return PermissionConfirmation.DENY
        choices = {
            "/exit": PermissionConfirmation.EXIT,
            "o": PermissionConfirmation.ONCE,
            "s": PermissionConfirmation.SESSION,
            "p": PermissionConfirmation.PERMANENT,
            "d": PermissionConfirmation.DENY,
        }
        return choices.get(choice, PermissionConfirmation.DENY)

    def render_delta(self, event: VisibleDelta) -> None:
        if isinstance(event, ThinkingDelta):
            self._render_thinking_delta(event)
        else:
            self._render_answer_delta(event)
        self._flush()
        self._turn_open = True

    def render_event(self, event: TurnEvent) -> None:
        """渲染一轮中可见的文本或工具状态。"""

        if isinstance(event, (ThinkingDelta, TextDelta)):
            self.render_delta(event)
        elif isinstance(event, AgentProgress):
            self._render_progress(event)
        elif isinstance(event, ToolCallRequested):
            self._render_tool_requested(event)
        elif isinstance(event, ToolExecutionStarted):
            self._render_tool_started(event)
        elif isinstance(event, ToolExecutionFinished):
            self._render_tool_finished(event)
        elif isinstance(event, TokenUsageReported):
            self._render_token_usage(event)
        elif isinstance(event, AgentStopped):
            self._render_agent_stopped(event)
        elif isinstance(event, PermissionStatus):
            self._render_permission_status(event)
        elif isinstance(event, CommandNotice):
            self._render_command_notice(event)
        elif isinstance(event, CommandHelp):
            self._render_command_help(event)
        elif isinstance(event, CommandStatus):
            self._render_command_status(event)
        elif isinstance(event, CommandMemory):
            self._render_command_memory(event)
        elif isinstance(event, CommandSession):
            self._render_command_session(event)
        elif isinstance(event, HookListEvent):
            self._render_hook_list(event)
        elif isinstance(event, SkillListEvent):
            self._render_skill_list(event)
        elif isinstance(event, RuntimeModeChanged):
            self._render_runtime_mode_changed(event)

    def set_command_registry(self, registry: CommandRegistry) -> None:
        self._command_registry = registry
        if self._session is None:
            return

    def set_runtime_mode(self, mode: RuntimeMode) -> None:
        self._runtime_mode = mode

    def clear_screen(self) -> None:
        self._finish_line()
        self._console.clear()
        self._reset_turn()

    def finish_turn(self) -> None:
        thinking_closed = self._close_thinking_section()
        if self._turn_open and not thinking_closed:
            self._console.print()
        self._reset_turn()

    def show_error(self, error: ProviderError) -> None:
        self._finish_line()
        suffix = ""
        if error.status_code is not None:
            suffix += f"（HTTP {error.status_code}）"
        if error.request_id:
            suffix += f"（请求 ID: {error.request_id}）"
        self._console.print(f"错误：{error.safe_message}{suffix}")
        self._reset_turn()

    def show_cancelled(self) -> None:
        self._finish_line()
        self._console.print("生成已取消，未保存本轮。")
        self._reset_turn()

    def show_config_error(self, message: str) -> None:
        self._console.print(f"配置错误：{message}")

    def show_startup_error(self) -> None:
        self._console.print("启动失败，请检查配置和依赖。")

    def show_runtime_error(self, error: Exception) -> None:
        self._finish_line()
        self._console.print(f"运行错误：{type(error).__name__}: {error}", style="red")
        self._reset_turn()

    def show_mcp_warning(self, warning: McpDiscoveryWarning) -> None:
        """显示不包含连接详情或凭据的 MCP Server 启动告警。"""

        self._console.print(
            f"MCP：Server {warning.server_name} 在{warning.phase}阶段不可用。{warning.message}",
            style="yellow",
        )

    def show_goodbye(self) -> None:
        self._finish_line()
        self._console.print("已退出 OkCode。")
        self._reset_turn()

    def _finish_line(self) -> None:
        thinking_closed = self._close_thinking_section()
        if self._turn_open and not thinking_closed:
            self._console.print()

    def _reset_turn(self) -> None:
        self._seen_sections.clear()
        self._turn_open = False

    def _render_thinking_delta(self, event: ThinkingDelta) -> None:
        if "thinking" not in self._seen_sections:
            if self._turn_open:
                self._console.print()
            self._console.print("[思考：", end="", style="bright_green")
            self._seen_sections.add("thinking")
        self._console.print(
            event.delta,
            end="",
            style="bright_green",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    def _render_answer_delta(self, event: VisibleDelta) -> None:
        if "answer" not in self._seen_sections:
            self._close_thinking_section()
            self._console.print(Rule(style="dim"))
            self._console.print("回答：", end=" ")
            self._seen_sections.add("answer")
        self._console.print(event.delta, end="", markup=False, highlight=False, soft_wrap=True)

    def _render_progress(self, event: AgentProgress) -> None:
        self._finish_line()
        self._console.print(f"进度：{event.message}", style="dim")
        self._turn_open = True

    def _render_tool_requested(self, event: ToolCallRequested) -> None:
        self._finish_line()
        self._console.print(
            f"工具：模型请求 {event.call.name}（#{event.index + 1}）",
            style="cyan",
        )
        self._turn_open = True

    def _render_tool_started(self, event: ToolExecutionStarted) -> None:
        self._finish_line()
        self._console.print(f"工具：正在执行 {event.tool_name}...", style="cyan")
        self._turn_open = True

    def _render_tool_finished(self, event: ToolExecutionFinished) -> None:
        result = event.result
        if result.error_code is ToolErrorCode.PERMISSION_DENIED or not result.data.get(
            "executed", True
        ):
            source = result.data.get("permission_source", "权限系统")
            self._finish_line()
            self._console.print(f"工具：{result.tool_name} 未执行（{source}）。{result.content}")
            self._turn_open = True
            return
        status = "完成" if result.success else "失败"
        summary = " ".join(result.content.split())
        if len(summary) > 160:
            summary = summary[:160] + "..."
        self._console.print(f"工具：{result.tool_name} {status}。{summary}")
        self._turn_open = True

    def _render_token_usage(self, event: TokenUsageReported) -> None:
        self._finish_line()
        usage = event.usage
        if not usage.available and not usage.cache.available:
            self._console.print(f"Token：第 {event.iteration} 轮用量不可用。", style="dim")
        else:
            parts: list[str] = []
            if usage.input_tokens is not None:
                parts.append(f"输入 {usage.input_tokens}")
            if usage.output_tokens is not None:
                parts.append(f"输出 {usage.output_tokens}")
            if usage.total_tokens is not None:
                parts.append(f"总计 {usage.total_tokens}")
            if usage.cache.available:
                if usage.cache.read_tokens is not None:
                    parts.append(f"缓存读取 {usage.cache.read_tokens}")
                if usage.cache.write_tokens is not None:
                    parts.append(f"缓存写入 {usage.cache.write_tokens}")
            if not parts:
                parts.append("用量不可用")
            summary = f"Token：第 {event.iteration} 轮，" + "，".join(parts) + "。"
            self._console.print(summary, style="dim")
        self._turn_open = True

    def _render_agent_stopped(self, event: AgentStopped) -> None:
        self._finish_line()
        self._console.print(f"停止：{event.message}", style="yellow")
        self._turn_open = True

    def _render_permission_status(self, event: PermissionStatus) -> None:
        self._finish_line()
        self._console.print(
            f"权限：当前 {event.current_mode}，默认 {event.default_mode}。", style="dim"
        )
        self._console.print(f"用户规则：{event.user_rules_path}", style="dim")
        self._console.print(f"项目规则：{event.project_rules_path}", style="dim")
        self._console.print(f"本地规则：{event.local_rules_path}", style="dim")
        if event.message:
            self._console.print(f"权限：{event.message}", style="yellow")
        self._turn_open = True

    def _render_command_notice(self, event: CommandNotice) -> None:
        self._finish_line()
        style = "yellow" if event.level.value != "info" else "dim"
        self._console.print(f"命令：{event.message}", style=style)
        self._turn_open = True

    def _render_command_help(self, event: CommandHelp) -> None:
        self._finish_line()
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("命令", no_wrap=True)
        table.add_column("描述")
        for entry in event.entries:
            table.add_row("/" + entry.name, entry.description)
        self._console.print(table)
        self._turn_open = True

    def _render_command_status(self, event: CommandStatus) -> None:
        self._finish_line()
        table = Table(show_header=False)
        table.add_column("字段", no_wrap=True)
        table.add_column("值")
        table.add_row("当前权限模式", event.permission_mode)
        table.add_row(
            "累计 Token",
            f"输入 {event.cumulative_input_tokens} / 输出 {event.cumulative_output_tokens}",
        )
        table.add_row("可用工具数量", str(event.available_tool_count))
        table.add_row("已加载记忆条目数", str(event.loaded_memory_item_count))
        table.add_row("当前模型名", event.model_name)
        table.add_row("当前工作目录", event.working_directory)
        self._console.print(table)
        self._turn_open = True

    def _render_command_memory(self, event: CommandMemory) -> None:
        self._finish_line()
        project = ", ".join(event.project_memory_files) or "（无）"
        user = ", ".join(event.user_memory_files) or "（无）"
        self._console.print(f"项目记忆：{project}", style="dim")
        self._console.print(f"用户记忆：{user}", style="dim")
        self._turn_open = True

    def _render_command_session(self, event: CommandSession) -> None:
        self._finish_line()
        self._console.print(f"会话 ID：{event.session_id or '（未启用）'}", style="dim")
        self._console.print(f"存档文件：{event.journal_path or '（未启用）'}", style="dim")
        self._turn_open = True

    def _render_hook_list(self, event: HookListEvent) -> None:
        self._finish_line()
        if not event.entries:
            self._console.print("Hook：当前未加载 Hook 规则。", style="dim")
            self._console.print(f"配置文件：{event.config_path}", style="dim")
            self._turn_open = True
            return
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("规则", no_wrap=True)
        table.add_column("事件", no_wrap=True)
        table.add_column("条件")
        table.add_column("动作", no_wrap=True)
        table.add_column("启用", no_wrap=True)
        table.add_column("once", no_wrap=True)
        table.add_column("后台", no_wrap=True)
        table.add_column("超时", justify="right", no_wrap=True)
        table.add_column("SubAgent", no_wrap=True)
        for entry in event.entries:
            table.add_row(
                entry.identifier,
                entry.event,
                entry.condition,
                entry.action,
                "是" if entry.enabled else "否",
                "是" if entry.once else "否",
                "是" if entry.background else "否",
                f"{entry.timeout_seconds:g}s",
                "占位" if entry.subagent_placeholder else "-",
            )
        self._console.print(f"配置文件：{event.config_path}", style="dim")
        self._console.print(table)
        self._turn_open = True

    def _render_skill_list(self, event: SkillListEvent) -> None:
        self._finish_line()
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Skill", no_wrap=True)
        table.add_column("来源", no_wrap=True)
        table.add_column("状态", no_wrap=True)
        table.add_column("说明")
        for entry in event.entries:
            table.add_row(
                entry.name,
                entry.source,
                "已激活" if entry.active else "可加载",
                entry.description,
            )
        self._console.print(table)
        for issue in event.issues:
            self._console.print(f"Skill 问题：{issue}", style="yellow")
        self._turn_open = True

    def _render_runtime_mode_changed(self, event: RuntimeModeChanged) -> None:
        self._finish_line()
        try:
            self._runtime_mode = RuntimeMode(event.mode)
        except ValueError:
            pass
        self._console.print(f"模式：{event.message}", style="dim")
        self._turn_open = True

    def _close_thinking_section(self) -> bool:
        if "thinking" in self._seen_sections and "thinking_closed" not in self._seen_sections:
            self._console.print("]", end="", style="bright_green")
            self._console.print()
            self._seen_sections.add("thinking_closed")
            return True
        return False

    def _flush(self) -> None:
        file: TextIO | object = self._console.file
        flush = getattr(file, "flush", None)
        if callable(flush):
            flush()

    def _new_prompt_session(self) -> PromptSession:
        kwargs = {
            "history": InMemoryHistory(),
            "bottom_toolbar": self._bottom_toolbar,
        }
        if self._command_registry is not None:
            kwargs["completer"] = SlashCommandCompleter(self._command_registry)
        return PromptSession(**kwargs)

    def _bottom_toolbar(self) -> str:
        return self._runtime_mode.marker
