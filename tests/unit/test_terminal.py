from __future__ import annotations

import re
from io import StringIO

from rich.console import Console

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.mcp.models import McpDiscoveryWarning
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    PermissionStatus,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    TokenUsageReported,
    ToolCall,
    ToolCallRequested,
    ToolExecutionFinished,
    ToolExecutionStarted,
)
from okcode.permissions.models import PermissionConfirmation, PermissionRequest
from okcode.prompt import PromptCacheUsage
from okcode.terminal import TerminalUI
from okcode.tools.models import PermissionTargetKind, ToolErrorCode, ToolExecutionResult


class StubSession:
    def __init__(self, values: list[object]) -> None:
        self._values = iter(values)

    def prompt(self, _: str) -> str:
        value = next(self._values)
        if isinstance(value, BaseException):
            raise value
        return str(value)

    async def prompt_async(self, prompt: str) -> str:
        return self.prompt(prompt)


def _ui(values: list[object] | None = None) -> tuple[TerminalUI, StringIO]:
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system="standard", width=40)
    return TerminalUI(console=console, session=StubSession(values or [])), output


def _plain_text(output: StringIO) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())


def test_thinking_is_bracketed_green_and_answer_is_separated() -> None:
    ui, output = _ui()
    ui.render_delta(ThinkingDelta("思考"))
    ui.render_delta(ThinkingDelta("继续"))
    ui.render_delta(TextDelta("[red]原文[/red]"))
    ui.finish_turn()
    text = _plain_text(output)
    assert "[思考：思考继续]" in text
    assert "\x1b[92m" in output.getvalue()
    assert text.index("]") < text.index("─") < text.index("回答：")
    assert "[red]原文[/red]" in text


def test_answer_is_separated_when_thinking_is_not_present() -> None:
    ui, output = _ui()
    ui.render_delta(TextDelta("正式回答"))
    ui.finish_turn()
    text = _plain_text(output)
    assert text.index("─") < text.index("回答： 正式回答")


def test_error_closes_an_open_thinking_section() -> None:
    ui, output = _ui()
    ui.render_delta(ThinkingDelta("未完成的思考"))
    ui.show_error(ProviderError(ProviderErrorKind.BAD_REQUEST, "请求错误"))
    text = _plain_text(output)
    assert "[思考：未完成的思考]" in text


def test_prompt_retries_after_interrupt_and_eof_exits() -> None:
    ui, _ = _ui([KeyboardInterrupt(), "hello", EOFError()])
    assert ui.prompt() == "hello"
    assert ui.prompt() is None


def test_error_hides_raw_secret() -> None:
    ui, output = _ui()
    ui.show_error(ProviderError(ProviderErrorKind.BAD_REQUEST, "安全消息", status_code=400))
    assert "安全消息" in output.getvalue()
    assert "OKCODE_SECRET_DO_NOT_PRINT_7429" not in output.getvalue()


def test_mcp_warning_shows_only_safe_server_diagnostic() -> None:
    ui, output = _ui()
    ui.show_mcp_warning(McpDiscoveryWarning("remote", "初始化", "MCP Server 在初始化阶段失败。"))
    text = " ".join(_plain_text(output).split())
    assert "MCP：Server remote 在初始化阶段不可用" in text
    assert "MCP Server 在初始化阶段失败。" in text
    assert "Authorization" not in text


def test_tool_events_show_short_status_without_full_json() -> None:
    ui, output = _ui()
    result = ToolExecutionResult(
        "call",
        "read_file",
        False,
        "文件不存在。",
        ToolErrorCode.NOT_FOUND,
        {"content": "不应显示的完整 JSON"},
    )

    ui.render_event(ToolExecutionStarted("read_file"))
    ui.render_event(ToolExecutionFinished(result))
    ui.finish_turn()

    text = _plain_text(output)
    assert "正在执行 read_file" in text
    assert "read_file 失败。文件不存在。" in text
    assert "不应显示的完整 JSON" not in text


def test_agent_events_show_progress_usage_and_stop_message() -> None:
    ui, output = _ui()
    ui.render_event(AgentProgress("模型迭代 1/12", 1))
    ui.render_event(ToolCallRequested(ToolCall("call", "read_file", "{}"), 0))
    ui.render_event(TokenUsageReported(TokenUsage.unavailable(), 1))
    ui.render_event(
        AgentStopped(AgentStopReason.NO_SAVED_PLAN, "没有可执行的计划，请先使用 /plan 生成计划。")
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "进度：模型迭代 1/12" in text
    assert "模型请求 read_file" in text
    assert "Token：第 1 轮用量不可用。" in text
    assert "停止：没有可执行的计划" in text


def test_token_usage_shows_real_cache_fields_when_available() -> None:
    ui, output = _ui()
    ui.render_event(
        TokenUsageReported(
            TokenUsage(
                input_tokens=20,
                output_tokens=3,
                total_tokens=23,
                available=True,
                cache=PromptCacheUsage(read_tokens=12, write_tokens=8, available=True),
            ),
            1,
        )
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "输入 20" in text
    assert "缓存读取 12" in text
    assert "缓存写入 8" in text


def _permission_request() -> PermissionRequest:
    return PermissionRequest(
        ToolCall("call", "run_command", "{}"),
        object(),  # type: ignore[arg-type]
        {"command": "git status"},
        PermissionTargetKind.COMMAND,
        "git status",
        "git status",
    )


async def test_permission_confirmation_maps_choices_and_safe_failures_to_deny() -> None:
    choices = {
        "o": PermissionConfirmation.ONCE,
        "s": PermissionConfirmation.SESSION,
        "p": PermissionConfirmation.PERMANENT,
        "d": PermissionConfirmation.DENY,
        "/exit": PermissionConfirmation.EXIT,
        "unexpected": PermissionConfirmation.DENY,
    }
    for value, expected in choices.items():
        ui, _ = _ui([value])
        assert await ui.confirm_permission(_permission_request()) is expected

    ui, _ = _ui([EOFError()])
    assert await ui.confirm_permission(_permission_request()) is PermissionConfirmation.DENY

    ui, output = _ui(["d"])
    _ = await ui.confirm_permission(_permission_request())
    assert "可能修改项目或影响系统" in _plain_text(output)


def test_permission_status_and_denied_tool_are_rendered_without_full_data() -> None:
    ui, output = _ui()
    ui.render_event(
        PermissionStatus(
            "default",
            "default",
            "C:/user/.okcode/permissions.yaml",
            "project/.okcode/permissions.yaml",
            "project/.okcode/permissions.local.yaml",
        )
    )
    ui.render_event(
        ToolExecutionFinished(
            ToolExecutionResult(
                "call",
                "run_command",
                False,
                "调用被权限规则拒绝。",
                ToolErrorCode.PERMISSION_DENIED,
                {"permission_source": "project", "executed": False, "secret": "不应显示"},
            )
        )
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "权限：当前 default，默认 default。" in text
    assert "工具：run_command" in text
    assert "未执行（project）" in text
    assert "不应显示" not in text
