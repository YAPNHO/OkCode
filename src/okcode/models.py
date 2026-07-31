"""OkCode 的领域模型。"""

from dataclasses import dataclass, field
from enum import StrEnum

from okcode.prompt.builder import PromptBundle
from okcode.prompt.cache import PromptCachePolicy, PromptCacheUsage
from okcode.tools.models import ToolDefinition, ToolExecutionResult


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
    prompt_cache: bool = False


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
class TokenUsage:
    """一次模型请求的 Token 用量。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    available: bool = False
    cache: PromptCacheUsage = field(default_factory=PromptCacheUsage.unavailable)

    @classmethod
    def unavailable(cls) -> "TokenUsage":
        return cls()


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """统一会话消息；私有状态仅由生成它的 Provider 使用。"""

    role: Role
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolExecutionResult | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolExecutionResult, ...] = ()
    provider_state: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        tool_calls = self.tool_calls
        tool_results = self.tool_results
        if self.tool_call is not None:
            if tool_calls and tool_calls != (self.tool_call,):
                raise ValueError("单个工具调用和工具调用集合不一致。")
            tool_calls = (self.tool_call,)
            object.__setattr__(self, "tool_calls", tool_calls)
        elif tool_calls:
            object.__setattr__(self, "tool_call", tool_calls[0])

        if self.tool_result is not None:
            if tool_results and tool_results != (self.tool_result,):
                raise ValueError("单个工具结果和工具结果集合不一致。")
            tool_results = (self.tool_result,)
            object.__setattr__(self, "tool_results", tool_results)
        elif tool_results:
            object.__setattr__(self, "tool_result", tool_results[0])

        if self.role is Role.USER and (
            not self.content or tool_calls or tool_results or self.tool_call or self.tool_result
        ):
            raise ValueError("用户消息必须只包含非空文本。")
        if self.role is Role.ASSISTANT:
            if tool_results or self.tool_result or (not self.content and not tool_calls):
                raise ValueError("助手消息必须包含文本或工具调用，且不能包含工具结果。")
        if self.role is Role.TOOL and (not tool_results or self.content or tool_calls):
            raise ValueError("工具消息必须只包含工具结果。")


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """一次模型调用的普通历史、提示包、工具和缓存策略。"""

    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...]
    prompt: PromptBundle
    cache: PromptCachePolicy
    model_override: str | None = None


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
    usage: TokenUsage = field(default_factory=TokenUsage.unavailable)


@dataclass(frozen=True, slots=True)
class AgentProgress:
    """Agent 运行进度事件。"""

    message: str
    iteration: int | None = None


@dataclass(frozen=True, slots=True)
class ToolCallRequested:
    """模型请求执行工具，供界面观察调度过程。"""

    call: ToolCall
    index: int


@dataclass(frozen=True, slots=True)
class TokenUsageReported:
    """一轮模型请求结束后的 Token 用量事件。"""

    usage: TokenUsage
    iteration: int


class AgentStopReason(StrEnum):
    """Agent 非成功停止原因。"""

    NO_SAVED_PLAN = "no_saved_plan"
    ITERATION_LIMIT = "iteration_limit"
    UNKNOWN_TOOL_LIMIT = "unknown_tool_limit"
    CONTEXT_COMPACTION_FAILED = "context_compaction_failed"
    CONTEXT_SUMMARY_CIRCUIT_OPEN = "context_summary_circuit_open"
    SESSION_ARCHIVE_FAILED = "session_archive_failed"
    SESSION_RESTORE_FAILED = "session_restore_failed"


@dataclass(frozen=True, slots=True)
class AgentStopped:
    """Agent 因非异常、非最终文本条件停止。"""

    reason: AgentStopReason
    message: str


@dataclass(frozen=True, slots=True)
class ToolExecutionStarted:
    """会话开始执行工具，供终端展示状态。"""

    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolExecutionFinished:
    """会话完成工具执行，供终端展示摘要。"""

    result: ToolExecutionResult


@dataclass(frozen=True, slots=True)
class PermissionStatus:
    """当前会话权限模式及规则文件位置。"""

    current_mode: str
    default_mode: str
    user_rules_path: str
    project_rules_path: str
    local_rules_path: str
    message: str | None = None


class CommandNoticeLevel(StrEnum):
    """命令提示的可见级别。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CommandNotice:
    """命令产生的普通提示。"""

    message: str
    level: CommandNoticeLevel = CommandNoticeLevel.INFO


@dataclass(frozen=True, slots=True)
class CommandHelpEntry:
    """帮助列表中的一行。"""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class CommandHelp:
    """命令帮助列表。"""

    entries: tuple[CommandHelpEntry, ...]


@dataclass(frozen=True, slots=True)
class CommandStatus:
    """/status 的固定六项状态。"""

    permission_mode: str
    cumulative_input_tokens: int
    cumulative_output_tokens: int
    available_tool_count: int
    loaded_memory_item_count: int
    model_name: str
    working_directory: str


@dataclass(frozen=True, slots=True)
class CommandMemory:
    """/memory 的记忆文件名列表。"""

    project_memory_files: tuple[str, ...]
    user_memory_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommandSession:
    """/session 的当前会话标识。"""

    session_id: str
    journal_path: str


@dataclass(frozen=True, slots=True)
class SkillListEntry:
    """/skill 列表中的一行。"""

    name: str
    description: str
    source: str
    active: bool
    version_id: str


@dataclass(frozen=True, slots=True)
class SkillListEvent:
    """/skill 命令输出。"""

    entries: tuple[SkillListEntry, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeModeChanged:
    """运行时模式切换提示。"""

    mode: str
    message: str


type VisibleDelta = ThinkingDelta | TextDelta
type StreamEvent = ThinkingDelta | TextDelta | StreamCompleted
type TurnEvent = (
    ThinkingDelta
    | TextDelta
    | AgentProgress
    | ToolCallRequested
    | ToolExecutionStarted
    | ToolExecutionFinished
    | TokenUsageReported
    | AgentStopped
    | PermissionStatus
    | CommandNotice
    | CommandHelp
    | CommandStatus
    | CommandMemory
    | CommandSession
    | SkillListEvent
    | RuntimeModeChanged
)
