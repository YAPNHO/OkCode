"""用户输入到命令或 Agent 的分流。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from okcode.commands.models import CommandContext, CommandResult
from okcode.commands.parser import CommandParser
from okcode.commands.registry import CommandRegistry
from okcode.models import CommandNotice


class DispatchResultKind(StrEnum):
    EMPTY = "empty"
    TEXT = "text"
    COMMAND = "command"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    kind: DispatchResultKind
    text: str | None = None
    command_result: CommandResult | None = None


class CommandDispatcher:
    """统一分发普通输入和斜杠命令。"""

    def __init__(
        self,
        registry: CommandRegistry,
        parser: CommandParser | None = None,
    ) -> None:
        self._registry = registry
        self._parser = parser or CommandParser()

    async def dispatch(self, text: str, context: CommandContext) -> DispatchResult:
        parsed = self._parser.parse(text)
        if parsed.empty:
            return DispatchResult(DispatchResultKind.EMPTY)
        if not parsed.is_command:
            return DispatchResult(DispatchResultKind.TEXT, text=parsed.text)
        assert parsed.command is not None
        command = self._registry.resolve(parsed.command.name)
        if command is None:
            notice = CommandNotice(f"未知命令：/{parsed.command.name}。输入 /help 查看可用命令。")
            return DispatchResult(
                DispatchResultKind.COMMAND,
                command_result=CommandResult(events=(notice,)),
            )
        return DispatchResult(
            DispatchResultKind.COMMAND,
            command_result=command.handler(context, parsed.command),
        )
