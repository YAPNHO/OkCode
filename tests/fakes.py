"""不访问网络的测试替身。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from okcode.models import ChatMessage, StreamEvent
from okcode.tools.models import ToolDefinition


class FakeProvider:
    def __init__(self, events: list[StreamEvent | Exception]) -> None:
        self.events = events
        self.requests: list[tuple[ChatMessage, ...]] = []
        self.tools: list[tuple[ToolDefinition, ...]] = []
        self.stream_closed = False
        self.closed = False

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> AsyncIterator[StreamEvent]:
        self.requests.append(tuple(messages))
        self.tools.append(tuple(tools))
        return self._stream()

    async def _stream(self) -> AsyncIterator[StreamEvent]:
        try:
            for event in self.events:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            self.stream_closed = True

    async def aclose(self) -> None:
        self.closed = True


class FakeTerminal:
    def __init__(self, prompts: list[str | None]) -> None:
        self._prompts = iter(prompts)
        self.welcome = []
        self.deltas = []
        self.errors = []
        self.cancelled = 0
        self.finished = 0
        self.goodbyes = 0

    def prompt(self) -> str | None:
        return next(self._prompts)

    def show_welcome(self, config: object) -> None:
        self.welcome.append(config)

    def render_delta(self, event: object) -> None:
        self.deltas.append(event)

    def render_event(self, event: object) -> None:
        self.deltas.append(event)

    def finish_turn(self) -> None:
        self.finished += 1

    def show_error(self, error: object) -> None:
        self.errors.append(error)

    def show_cancelled(self) -> None:
        self.cancelled += 1

    def show_goodbye(self) -> None:
        self.goodbyes += 1
