"""OkCode 的领域模型。"""

from dataclasses import dataclass, field
from enum import StrEnum

from okcode.tools.models import ToolExecutionResult


class ProviderProtocol(StrEnum):
    """第一阶段支持的供应商协议。"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """单个供应商配置。"""

    name: str
    protocol: ProviderProtocol
    model: str
    base_url: str
    api_key: str = field(repr=False)
    thinking: bool = False


@dataclass(frozen=True, slots=True)
class AppConfig:
    """应用配置及当前选中的供应商。"""

    active: str
    providers: tuple[ProviderConfig, ...]

    @property
    def active_provider(self) -> ProviderConfig:
        for provider in self.providers:
            if provider.name == self.active:
                return provider
        raise LookupError(f"不存在名为 {self.active!r} 的供应商配置")


class Role(StrEnum):
    """对话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求执行的一次工具调用。"""

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """统一会话消息；私有状态仅由生成它的 Provider 使用。"""

    role: Role
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolExecutionResult | None = None
    provider_state: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.role is Role.USER and (not self.content or self.tool_call or self.tool_result):
            raise ValueError("用户消息必须只包含非空文本。")
        if self.role is Role.ASSISTANT:
            if self.tool_result or (not self.content and self.tool_call is None):
                raise ValueError("助手消息必须包含文本或工具调用，且不能包含工具结果。")
        if self.role is Role.TOOL and (self.tool_result is None or self.content or self.tool_call):
            raise ValueError("工具消息必须只包含工具结果。")


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    """流式思考增量。"""

    delta: str


@dataclass(frozen=True, slots=True)
class TextDelta:
    """流式正式回答增量。"""

    delta: str


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    """Provider 确认流完整结束后的最终助手消息。"""

    message: ChatMessage


@dataclass(frozen=True, slots=True)
class ToolExecutionStarted:
    """会话开始执行工具，供终端展示状态。"""

    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolExecutionFinished:
    """会话完成工具执行，供终端展示摘要。"""

    result: ToolExecutionResult


type VisibleDelta = ThinkingDelta | TextDelta
type StreamEvent = ThinkingDelta | TextDelta | StreamCompleted
type TurnEvent = ThinkingDelta | TextDelta | ToolExecutionStarted | ToolExecutionFinished
