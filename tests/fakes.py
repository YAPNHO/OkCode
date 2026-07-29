"""不访问网络的测试替身。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from okcode.models import ChatMessage, ProviderRequest, StreamEvent
from okcode.tools.models import ToolDefinition


class FakeProvider:
    def __init__(
        self,
        events: list[StreamEvent | Exception] | list[list[StreamEvent | Exception]],
    ) -> None:
        if events and isinstance(events[0], list):
            self._scripts = [list(script) for script in events]  # type: ignore[list-item]
        else:
            self._scripts = [list(events)]  # type: ignore[arg-type]
        self.requests: list[tuple[ChatMessage, ...]] = []
        self.provider_requests: list[ProviderRequest] = []
        self.tools: list[tuple[ToolDefinition, ...]] = []
        self.stream_closed = False
        self.stream_closed_count = 0
        self.closed = False

    def stream(
        self,
        request: ProviderRequest,
    ) -> AsyncIterator[StreamEvent]:
        self.provider_requests.append(request)
        self.requests.append(request.messages)
        self.tools.append(request.tools)
        script = self._scripts.pop(0) if self._scripts else []
        return self._stream(script)

    async def _stream(self, script: list[StreamEvent | Exception]) -> AsyncIterator[StreamEvent]:
        try:
            for event in script:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            self.stream_closed = True
            self.stream_closed_count += 1

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
