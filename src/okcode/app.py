"""同步 REPL 与异步单轮生成编排。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from okcode.conversation import ConversationSession
from okcode.errors import ExitRequested, ProviderError
from okcode.models import ProviderConfig, TurnEvent
from okcode.terminal import TerminalUI


class OkCodeApp:
    """OkCode 的交互主循环。"""

    def __init__(
        self,
        ui: TerminalUI,
        conversation: ConversationSession,
        runner: asyncio.Runner,
        config: ProviderConfig,
    ) -> None:
        self._ui = ui
        self._conversation = conversation
        self._runner = runner
        self._config = config

    def run(self) -> int:
        self._ui.show_welcome(self._config, self._conversation.permission_mode)
        while True:
            text = self._ui.prompt()
            if text is None or text.strip() == "/exit":
                self._ui.show_goodbye()
                return 0
            if not text.strip():
                continue
            try:
                if text.strip() == "/resume":
                    session_id = self._ui.select_session(
                        self._conversation.list_resumable_sessions()
                    )
                    if session_id is not None:
                        self._runner.run(
                            self._consume_events(self._conversation.restore_session(session_id))
                        )
                    continue
                self._runner.run(self._consume_turn(text))
            except ExitRequested:
                self._ui.show_goodbye()
                return 0
            except KeyboardInterrupt:
                self._ui.show_cancelled()
            except ProviderError as error:
                self._ui.show_error(error)
            except Exception as error:
                self._ui.show_runtime_error(error)

    async def _consume_turn(self, text: str) -> None:
        await self._consume_events(self._conversation.stream_turn(text))

    async def _consume_events(self, events: AsyncIterator[TurnEvent]) -> None:
        async for event in events:
            self._ui.render_event(event)
        self._ui.finish_turn()
