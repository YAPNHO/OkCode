"""命令系统的数据模型和会话端口。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from okcode.models import TurnEvent
from okcode.sessions import SessionDescriptor


class CommandKind(StrEnum):
    """斜杠命令的执行类别。"""

    LOCAL = "local"
    UI = "ui"
    PROMPT = "prompt"


class RuntimeMode(StrEnum):
    """用户可见的运行时模式。"""

    DEFAULT = "default"
    PLAN = "plan"

    @property
    def marker(self) -> str:
        return "[PLAN]" if self is RuntimeMode.PLAN else "[DEFAULT]"


class ToolScope(StrEnum):
    """提示词命令对工具可见范围的要求。"""

    CURRENT_MODE = "current_mode"
    ALL = "all"
    READ_ONLY = "read_only"


class CommandUiAction(StrEnum):
    """命令需要应用层执行的 UI 或生命周期动作。"""

    NONE = "none"
    CLEAR_SCREEN = "clear_screen"
    EXIT = "exit"
    SELECT_SESSION = "select_session"
    RESET_SESSION = "reset_session"


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """一次斜杠输入解析结果。"""

    raw: str
    name: str
    args: str


@dataclass(frozen=True, slots=True)
class ForwardedUserMessage:
    """需要继续交给 Agent 的命令派生用户消息。"""

    content: str
    runtime_mode: RuntimeMode
    tool_scope: ToolScope = ToolScope.CURRENT_MODE
    preset_name: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """命令处理后的统一结果。"""

    events: tuple[TurnEvent, ...] = ()
    stream: AsyncIterator[TurnEvent] | None = None
    ui_action: CommandUiAction = CommandUiAction.NONE
    forward: ForwardedUserMessage | None = None


CommandHandler = Callable[["CommandContext", ParsedCommand], CommandResult]


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    """一条已注册命令的元数据和处理入口。"""

    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: CommandKind
    argument_hint: str | None
    hidden: bool
    handler: CommandHandler


@dataclass(frozen=True, slots=True)
class CompletionCandidate:
    """命令补全候选。"""

    text: str
    display: str
    description: str


@dataclass(frozen=True, slots=True)
class CommandStatusSnapshot:
    """/status 展示所需的固定六项信息。"""

    permission_mode: str
    cumulative_input_tokens: int
    cumulative_output_tokens: int
    available_tool_count: int
    loaded_memory_item_count: int
    model_name: str
    working_directory: str


@dataclass(frozen=True, slots=True)
class CommandMemorySnapshot:
    """/memory 展示的记忆文件名列表。"""

    project_memory_files: tuple[str, ...]
    user_memory_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommandSessionSnapshot:
    """当前会话的用户可见标识。"""

    session_id: str
    journal_path: str


class CommandConversationPort(Protocol):
    """命令层可使用的会话能力。"""

    @property
    def runtime_mode(self) -> RuntimeMode: ...

    def set_runtime_mode(self, mode: RuntimeMode) -> None: ...
    def status_snapshot(self) -> CommandStatusSnapshot: ...
    def memory_snapshot(self) -> CommandMemorySnapshot: ...
    def permission_string(self) -> str: ...
    def session_snapshot(self) -> CommandSessionSnapshot: ...
    def list_resumable_sessions(self) -> tuple[SessionDescriptor, ...]: ...
    def restore_session(self, session_id: str) -> AsyncIterator[TurnEvent]: ...
    def stream_manual_compaction(self) -> AsyncIterator[TurnEvent]: ...
    def reset_session(self) -> TurnEvent: ...
    def stream_do_instruction(self) -> AsyncIterator[TurnEvent]: ...
    def stream_user_message(
        self,
        text: str,
        *,
        mode: RuntimeMode | None = None,
        tool_scope: ToolScope | None = None,
    ) -> AsyncIterator[TurnEvent]: ...


@dataclass(slots=True)
class CommandContext:
    """命令处理函数的运行上下文。"""

    config: object
    registry: object
    conversation: CommandConversationPort
    workspace_root: Path
