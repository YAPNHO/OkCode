"""斜杠命令输入解析。"""

from __future__ import annotations

from dataclasses import dataclass

from okcode.commands.models import ParsedCommand


@dataclass(frozen=True, slots=True)
class ParseResult:
    """用户输入解析结果。"""

    empty: bool
    is_command: bool
    command: ParsedCommand | None
    text: str


class CommandParser:
    """识别并解析斜杠命令。"""

    def parse(self, text: str) -> ParseResult:
        stripped = text.strip()
        if not stripped:
            return ParseResult(True, False, None, text)
        if not stripped.startswith("/"):
            return ParseResult(False, False, None, text)
        head, _, tail = stripped.partition(" ")
        name = head[1:].lower()
        return ParseResult(
            False,
            True,
            ParsedCommand(raw=text, name=name, args=tail.strip()),
            text,
        )
