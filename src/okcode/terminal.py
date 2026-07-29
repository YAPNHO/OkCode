"""行内终端输入和 Rich 渲染。"""

from __future__ import annotations

from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.rule import Rule

from okcode.errors import ProviderError
from okcode.models import (
    ProviderConfig,
    TextDelta,
    ThinkingDelta,
    ToolExecutionFinished,
    ToolExecutionStarted,
    TurnEvent,
    VisibleDelta,
)


class TerminalUI:
    """保留普通终端滚动历史的最小交互界面。"""

    def __init__(
        self, *, console: Console | None = None, session: PromptSession | None = None
    ) -> None:
        self._console = console or Console()
        self._session = session
        self._seen_sections: set[str] = set()
        self._turn_open = False

    def prompt(self) -> str | None:
        """读取一轮输入；EOF 表示退出。"""

        while True:
            try:
                session = self._session
                if session is None:
                    session = PromptSession(history=InMemoryHistory())
                    self._session = session
                return session.prompt("你 > ")
            except KeyboardInterrupt:
                self._console.print()
            except EOFError:
                return None

    def show_welcome(self, config: ProviderConfig) -> None:
        self._console.print(f"OkCode | {config.name} | {config.protocol} | {config.model}")

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
        elif isinstance(event, ToolExecutionStarted):
            self._render_tool_started(event)
        elif isinstance(event, ToolExecutionFinished):
            self._render_tool_finished(event)

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

    def _render_tool_started(self, event: ToolExecutionStarted) -> None:
        self._finish_line()
        self._console.print(f"工具：正在执行 {event.tool_name}...", style="cyan")
        self._turn_open = True

    def _render_tool_finished(self, event: ToolExecutionFinished) -> None:
        result = event.result
        status = "完成" if result.success else "失败"
        summary = " ".join(result.content.split())
        if len(summary) > 160:
            summary = summary[:160] + "..."
        self._console.print(f"工具：{result.tool_name} {status}。{summary}")
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
