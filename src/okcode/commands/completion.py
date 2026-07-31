"""prompt-toolkit 命令补全适配。"""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from okcode.commands.registry import CommandRegistry


class SlashCommandCompleter(Completer):
    """只在斜杠命令名位置提供补全。"""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        before = document.text_before_cursor
        if not before.startswith("/") or " " in before:
            return
        prefix = before[1:]
        for candidate in self._registry.completion_candidates(prefix):
            yield Completion(
                candidate.text,
                start_position=-len(before),
                display=candidate.display,
                display_meta=candidate.description,
            )
