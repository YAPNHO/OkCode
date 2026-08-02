"""同步 REPL 与异步单轮生成编排。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from okcode.commands import (
    CommandContext,
    CommandDispatcher,
    CommandRegistry,
    CommandUiAction,
    DispatchResultKind,
    build_default_command_registry,
)
from okcode.conversation import ConversationSession
from okcode.errors import ExitRequested, ProviderError
from okcode.hooks.models import HookContext, HookEvent
from okcode.hooks.runtime import HookRuntime
from okcode.models import ProviderConfig, TurnEvent
from okcode.terminal import TerminalUI

_LOG = logging.getLogger(__name__)


class OkCodeApp:
    """OkCode 的交互主循环。"""

    def __init__(
        self,
        ui: TerminalUI,
        conversation: ConversationSession,
        runner: asyncio.Runner,
        config: ProviderConfig,
        command_registry: CommandRegistry | None = None,
        skill_runtime: object | None = None,
        hooks: HookRuntime | None = None,
    ) -> None:
        self._ui = ui
        self._conversation = conversation
        self._runner = runner
        self._config = config
        self._command_registry = command_registry or build_default_command_registry()
        self._skill_runtime = skill_runtime
        self._hooks = hooks
        self._session_closed = False
        self._dispatcher = CommandDispatcher(self._command_registry)
        set_registry = getattr(self._ui, "set_command_registry", None)
        if callable(set_registry):
            set_registry(self._command_registry)

    def run(self) -> int:
        self._sync_terminal_state()
        self._dispatch_session_start()
        self._ui.show_welcome(self._config, self._conversation.permission_mode)
        while True:
            text = self._ui.prompt()
            if text is None:
                self._dispatch_session_end()
                self._ui.show_goodbye()
                return 0
            if self._is_exit_input(text):
                self._dispatch_session_end()
                self._ui.show_goodbye()
                return 0
            try:
                should_exit = self._runner.run(self._handle_input(text))
                if should_exit:
                    self._dispatch_session_end()
                    self._ui.show_goodbye()
                    return 0
            except ExitRequested:
                self._dispatch_session_end()
                self._ui.show_goodbye()
                return 0
            except KeyboardInterrupt:
                self._ui.show_cancelled()
            except ProviderError as error:
                self._dispatch_system_error(error)
                self._ui.show_error(error)
            except Exception as error:
                self._dispatch_system_error(error)
                self._ui.show_runtime_error(error)

    async def _handle_input(self, text: str) -> bool:
        context = CommandContext(
            self._config,
            self._command_registry,
            self._conversation,
            Path.cwd(),
            self._skill_runtime,
        )
        dispatched = await self._dispatcher.dispatch(text, context)
        if dispatched.kind is DispatchResultKind.EMPTY:
            return False
        if dispatched.kind is DispatchResultKind.TEXT:
            assert dispatched.text is not None
            await self._consume_events(self._conversation.stream_user_message(dispatched.text))
            return False
        assert dispatched.command_result is not None
        return await self._consume_command_result(dispatched.command_result)

    async def _consume_command_result(self, result: object) -> bool:
        from okcode.commands import CommandResult

        assert isinstance(result, CommandResult)
        rendered = False
        for event in result.events:
            self._ui.render_event(event)
            rendered = True
        if result.stream is not None:
            await self._consume_events(result.stream, finish=False)
            rendered = True
        if result.ui_action is CommandUiAction.EXIT:
            if rendered:
                self._ui.finish_turn()
            return True
        if result.ui_action is CommandUiAction.CLEAR_SCREEN:
            clear = getattr(self._ui, "clear_screen", None)
            if callable(clear):
                clear()
        elif result.ui_action is CommandUiAction.SELECT_SESSION:
            session_id = self._ui.select_session(self._conversation.list_resumable_sessions())
            if session_id is not None:
                await self._consume_events(
                    self._conversation.restore_session(session_id),
                    finish=False,
                )
                rendered = True
        elif result.ui_action is CommandUiAction.RESET_SESSION:
            self._ui.render_event(self._conversation.reset_session())
            rendered = True
        self._sync_terminal_state()
        if result.forward is not None:
            await self._consume_events(
                self._conversation.stream_user_message(
                    result.forward.content,
                    mode=result.forward.runtime_mode,
                    tool_scope=result.forward.tool_scope,
                ),
                finish=False,
            )
            rendered = True
        if rendered:
            self._ui.finish_turn()
        return False

    async def _consume_turn(self, text: str) -> None:
        await self._consume_events(self._conversation.stream_user_message(text))

    async def _consume_events(
        self,
        events: AsyncIterator[TurnEvent],
        *,
        finish: bool = True,
    ) -> None:
        async for event in events:
            self._ui.render_event(event)
        if finish:
            self._ui.finish_turn()

    def _sync_terminal_state(self) -> None:
        set_mode = getattr(self._ui, "set_runtime_mode", None)
        if callable(set_mode):
            set_mode(self._conversation.runtime_mode)
        set_permission = getattr(self._ui, "set_permission_mode", None)
        if callable(set_permission):
            set_permission(self._conversation.permission_mode)

    def _dispatch_session_start(self) -> None:
        self._dispatch_hook(
            HookContext(
                HookEvent.SESSION_START,
                {
                    "session.id": self._conversation.session_snapshot().session_id,
                    "runtime.mode": self._conversation.runtime_mode.value,
                },
            )
        )

    def _dispatch_session_end(self) -> None:
        if self._session_closed:
            return
        self._session_closed = True
        self._dispatch_hook(
            HookContext(
                HookEvent.SESSION_END,
                {
                    "session.id": self._conversation.session_snapshot().session_id,
                    "session.turn_count": self._conversation.turn_count,
                },
            )
        )

    def _dispatch_system_error(self, error: Exception) -> None:
        category = type(error).__name__
        message = str(error)
        if isinstance(error, ProviderError):
            category = error.kind.value
            message = error.safe_message
        self._dispatch_hook(
            HookContext(
                HookEvent.ERROR,
                {
                    "error.category": category,
                    "error.message": message,
                },
            )
        )

    def _dispatch_hook(self, context: HookContext) -> None:
        if self._hooks is None:
            return
        try:
            self._runner.run(self._hooks.dispatch(context))
        except Exception as exc:
            _LOG.info("Hook 事件分发失败：%s", exc)

    def _is_exit_input(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return False
        command = stripped.split(maxsplit=1)[0][1:].lower()
        definition = self._command_registry.resolve(command)
        return definition is not None and definition.name == "exit"
