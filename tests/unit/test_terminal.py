from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.mcp.models import McpDiscoveryWarning
from okcode.models import (
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ChatMessage,
    CoordinatorStatus,
    HookListEntry,
    HookListEvent,
    PermissionStatus,
    Role,
    SessionHistoryEvent,
    SkillListEntry,
    SkillListEvent,
    TeamStatusEntry,
    TeamStatusEvent,
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
from okcode.sessions import SessionDescriptor
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


def _ui(
    values: list[object] | None = None,
    *,
    width: int = 40,
) -> tuple[TerminalUI, StringIO]:
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system="standard", width=width)
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


def _session_descriptor(title: str = "\u4fee\u590d\u4f1a\u8bdd") -> SessionDescriptor:
    return SessionDescriptor(
        "20260730-100000-abcd",
        title,
        4,
        datetime(2026, 7, 30, 10, tzinfo=UTC),
    )


def test_session_selector_displays_table_and_retries_until_valid_number() -> None:
    ui, output = _ui(["invalid", "3", "1"], width=120)
    selected = ui.select_session(
        (_session_descriptor(), _session_descriptor("\u53e6\u4e00\u4e2a\u4f1a\u8bdd"))
    )

    assert selected == "20260730-100000-abcd"
    text = _plain_text(output)
    assert "\u53ef\u6062\u590d\u4f1a\u8bdd" in text
    assert "\u4f1a\u8bdd ID" in text
    assert "\u4fee\u590d\u4f1a\u8bdd" in text
    assert "\u8bf7\u8f93\u5165\u4f1a\u8bdd\u7f16\u53f7" in text
    assert "\u4f1a\u8bdd\u7f16\u53f7\u4e0d\u5b58\u5728" in text


def test_session_selector_handles_empty_list_and_cancellation() -> None:
    ui, output = _ui()
    assert ui.select_session(()) is None
    assert "\u6ca1\u6709\u53ef\u6062\u590d\u7684\u4f1a\u8bdd\u3002" in _plain_text(output)

    cancelled_ui, cancelled_output = _ui(["/cancel"])
    assert cancelled_ui.select_session((_session_descriptor(),)) is None
    assert "\u5df2\u53d6\u6d88\u6062\u590d\u3002" in _plain_text(cancelled_output)


def test_async_session_selector_works_inside_running_event_loop() -> None:
    async def run_selector() -> str | None:
        ui, output = _ui(["1"])
        selected = await ui.select_session_async((_session_descriptor(),))
        assert "\u53ef\u6062\u590d\u4f1a\u8bdd" in _plain_text(output)
        return selected

    assert asyncio.run(run_selector()) == "20260730-100000-abcd"


def test_error_hides_raw_secret() -> None:
    ui, output = _ui()
    ui.show_error(ProviderError(ProviderErrorKind.BAD_REQUEST, "安全消息", status_code=400))
    assert "安全消息" in output.getvalue()
    assert "OKCODE_SECRET_DO_NOT_PRINT_7429" not in output.getvalue()


def test_runtime_error_closes_open_line_and_shows_exception_type() -> None:
    ui, output = _ui()
    ui.render_event(AgentProgress("模型请求 1", 1))
    ui.show_runtime_error(ValueError("运行期异常"))

    text = _plain_text(output)
    assert "进度：模型请求 1" in text
    assert "运行错误：ValueError: 运行期异常" in text


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
    ui.render_event(AgentProgress("模型请求 1", 1))
    ui.render_event(ToolCallRequested(ToolCall("call", "read_file", "{}"), 0))
    ui.render_event(TokenUsageReported(TokenUsage.unavailable(), 1))
    ui.render_event(
        AgentStopped(AgentStopReason.NO_SAVED_PLAN, "没有可执行的计划，请先使用 /plan 生成计划。")
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "进度：模型请求 1" in text
    assert "模型请求 read_file" in text
    assert "Token：第 1 轮用量不可用。" in text
    assert "停止：没有可执行的计划" in text


def test_restored_session_history_renders_user_and_assistant_messages() -> None:
    ui, output = _ui(width=80)
    ui.render_event(
        SessionHistoryEvent(
            (
                ChatMessage(Role.USER, "之前的问题"),
                ChatMessage(Role.ASSISTANT, "之前的回答"),
            )
        )
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "已恢复会话历史" in text
    assert "用户：" in text
    assert "之前的问题" in text
    assert "助手：" in text
    assert "之前的回答" in text


def test_skill_list_renders_metadata_activation_and_issues() -> None:
    ui, output = _ui(width=120)
    ui.render_event(
        SkillListEvent(
            (SkillListEntry("review", "审查改动", "project", True, "v1"),),
            ("project:bad.md YAML 错误",),
        )
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "review" in text
    assert "project" in text
    assert "已激活" in text
    assert "Skill 问题" in text


def test_hook_list_renders_empty_state_and_loaded_rules() -> None:
    ui, output = _ui(width=140)
    ui.render_event(HookListEvent((), "D:/repo/.okcode/hooks.yaml"))
    ui.render_event(
        HookListEvent(
            (
                HookListEntry(
                    "guard-shell",
                    "tool.before",
                    "tool.name=exact:run_command",
                    "shell",
                    True,
                    True,
                    False,
                    3.0,
                    False,
                ),
                HookListEntry(
                    "notify-agent",
                    "turn.end",
                    "无条件",
                    "subagent",
                    False,
                    False,
                    True,
                    10.0,
                    True,
                ),
            ),
            "D:/repo/.okcode/hooks.yaml",
        )
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "Hook：当前未加载 Hook 规则。" in text
    assert "配置文件：D:/repo/.okcode/hooks.yaml" in text
    assert "guard-shell" in text
    assert "tool.before" in text
    assert "tool.name=exact:run_command" in text
    assert "shell" in text
    assert "notify-agent" in text
    assert "占位" in text


def test_team_status_renders_summary_members_unread_and_recovery() -> None:
    ui, output = _ui(width=140)
    ui.render_event(
        TeamStatusEvent(
            team_name="core",
            leader_session_id="lead-session",
            members=(
                TeamStatusEntry("worker", "builder", "coroutine", "idle", 2, True),
                TeamStatusEntry("reviewer", "review", "terminal_pane", "blocked", 0, False),
            ),
            task_count=3,
            blocked_task_count=1,
            updated_at="2026-08-02T10:00:00+00:00",
            message="团队状态",
        )
    )
    ui.finish_turn()

    text = _plain_text(output)
    assert "团队状态" in text
    assert "团队" in text
    assert "core" in text
    assert "lead-session" in text
    assert "worker" in text
    assert "reviewer" in text
    assert "未读" in text
    assert "可恢复" in text
    assert "2" in text
    assert "是" in text
    assert "否" in text


def test_team_status_renders_empty_member_notice() -> None:
    ui, output = _ui(width=100)
    ui.render_event(
        TeamStatusEvent(
            team_name="empty",
            leader_session_id="lead-session",
            members=(),
            task_count=0,
            blocked_task_count=0,
            updated_at="2026-08-02T10:00:00+00:00",
        )
    )
    ui.finish_turn()

    assert "团队成员：当前没有成员。" in _plain_text(output)


def test_coordinator_status_renders_enabled_and_disabled_states() -> None:
    ui, output = _ui(width=100)
    ui.render_event(CoordinatorStatus(True, "双锁模式生效"))
    ui.render_event(CoordinatorStatus(False, "等待环境变量"))
    ui.finish_turn()

    text = _plain_text(output)
    assert "coordinator" in text
    assert "已启用" in text
    assert "未启用" in text
    assert "双锁模式生效" in text
    assert "等待环境变量" in text


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
    text = _plain_text(output)
    assert "可能修改项目或影响系统" in text
    assert "s=本会话内允许此工具" in text.replace("\n", "")


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


def test_bottom_toolbar_reflects_permission_mode_updates() -> None:
    ui, _ = _ui()

    assert ui._bottom_toolbar() == "[模式:DEFAULT] [权限:DEFAULT]"

    ui.render_event(
        PermissionStatus(
            "allow",
            "default",
            "C:/user/.okcode/permissions.yaml",
            "project/.okcode/permissions.yaml",
            "project/.okcode/permissions.local.yaml",
        )
    )

    assert ui._bottom_toolbar() == "[模式:DEFAULT] [权限:ALLOW]"
