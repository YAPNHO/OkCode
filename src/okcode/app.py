"""同步 REPL 与异步单轮生成编排。"""

from __future__ import annotations

import asyncio

from okcode.conversation import ConversationSession
from okcode.errors import ProviderError
from okcode.models import ProviderConfig
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
                self._runner.run(self._consume_turn(text))
            except KeyboardInterrupt:
                self._ui.show_cancelled()
            except ProviderError as error:
                self._ui.show_error(error)

    async def _consume_turn(self, text: str) -> None:
        async for event in self._conversation.stream_turn(text):
            self._ui.render_event(event)
        self._ui.finish_turn()
