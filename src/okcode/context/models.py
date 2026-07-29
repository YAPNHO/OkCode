"""上下文管理使用的配置和会话状态模型。"""

from __future__ import annotations

from dataclasses import dataclass

from okcode.models import ChatMessage


@dataclass(frozen=True, slots=True)
class ContextConfig:
    """固定窗口下的上下文预算参数。"""

    context_window_tokens: int = 200_000
    automatic_compaction_tokens: int = 167_000
    summary_output_reserve_tokens: int = 20_000
    safety_margin_tokens: int = 13_000
    chars_per_token: int = 4
    max_tool_result_chars: int = 50_000
    max_tool_message_chars: int = 200_000
    retain_recent_tokens: int = 10_000
    retain_recent_messages: int = 5
    summary_failure_limit: int = 3

    def __post_init__(self) -> None:
        expected = (
            self.context_window_tokens
            - self.summary_output_reserve_tokens
            - self.safety_margin_tokens
        )
        if self.automatic_compaction_tokens != expected:
            raise ValueError("自动摘要阈值必须等于窗口减去输出预留和安全余量。")
        if (
            min(
                self.chars_per_token,
                self.max_tool_result_chars,
                self.max_tool_message_chars,
                self.retain_recent_tokens,
                self.retain_recent_messages,
                self.summary_failure_limit,
            )
            <= 0
        ):
            raise ValueError("上下文管理阈值必须为正数。")


@dataclass(frozen=True, slots=True)
class TokenEstimateAnchor:
    """最近一次正常请求返回的输入 Token Usage 锚点。"""

    input_tokens: int
    input_chars: int


@dataclass(slots=True)
class ConversationContextState:
    """仅在当前进程会话中有效的上下文状态。"""

    summary: str | None = None
    boundary_message: str | None = None
    original_user_messages: tuple[str, ...] = ()
    estimate_anchor: TokenEstimateAnchor | None = None
    consecutive_summary_failures: int = 0
    circuit_open: bool = False


@dataclass(frozen=True, slots=True)
class ToolResultArtifact:
    """外置工具结果的工作区相对位置和大小。"""

    relative_path: str
    original_chars: int


@dataclass(frozen=True, slots=True)
class SummaryPlan:
    """一次摘要调用的只读输入和原子提交边界。"""

    history_to_summarize: tuple[ChatMessage, ...]
    retained_history: tuple[ChatMessage, ...]
    transcript: str
    original_user_messages: tuple[str, ...]
