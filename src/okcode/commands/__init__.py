"""斜杠命令注册、解析和分发。"""

from okcode.commands.defaults import build_default_command_registry
from okcode.commands.dispatcher import CommandDispatcher, DispatchResult, DispatchResultKind
from okcode.commands.models import (
    CommandContext,
    CommandDefinition,
    CommandKind,
    CommandResult,
    CommandUiAction,
    CompletionCandidate,
    ForwardedUserMessage,
    ParsedCommand,
    RuntimeMode,
    ToolScope,
)
from okcode.commands.parser import CommandParser, ParseResult
from okcode.commands.registry import CommandRegistry

__all__ = [
    "CommandContext",
    "CommandDefinition",
    "CommandDispatcher",
    "CommandKind",
    "CommandParser",
    "CommandRegistry",
    "CommandResult",
    "CommandUiAction",
    "CompletionCandidate",
    "DispatchResult",
    "DispatchResultKind",
    "ForwardedUserMessage",
    "ParseResult",
    "ParsedCommand",
    "RuntimeMode",
    "ToolScope",
    "build_default_command_registry",
]
