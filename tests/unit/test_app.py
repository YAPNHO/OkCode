from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from okcode.app import OkCodeApp
from okcode.conversation import ConversationSession
from okcode.errors import ExitRequested, ProviderError, ProviderErrorKind
from okcode.models import (
    AgentProgress,
    ChatMessage,
    ProviderConfig,
    ProviderProtocol,
    Role,
    StreamCompleted,
    TextDelta,
)
from okcode.sessions import SessionStore
from okcode.tools.executor import ToolExecutor
from okcode.tools.registry import ToolRegistry
from tests.fakes import FakeProvider, FakeTerminal


def _config() -> ProviderConfig:
    return ProviderConfig(
        name="test",
        protocol=ProviderProtocol.OPENAI,
        model="test-model",
        base_url="https://example.test",
        api_key="secret",
    )


def _conversation(
    provider: FakeProvider, *, session_store: SessionStore | None = None
) -> ConversationSession:
    registry = ToolRegistry()
    return ConversationSession(
        provider,
        registry,
        ToolExecutor(registry),
        session_store=session_store,
        session_journal=session_store.create_journal() if session_store is not None else None,
    )


def test_successful_turn_and_exit() -> None:
    provider = FakeProvider(
        [TextDelta("回答"), StreamCompleted(ChatMessage(Role.ASSISTANT, "回答"))]
    )
    ui = FakeTerminal(["问题", "/exit"])
    runner = asyncio.Runner()
    try:
        app = OkCodeApp(ui, _conversation(provider), runner, _config())
        assert app.run() == 0
    finally:
        runner.close()
    assert len(provider.requests) == 1
    assert ui.deltas[0] == AgentProgress("模型请求 1", 1)
    assert TextDelta("回答") in ui.deltas
    assert ui.finished == 1
    assert ui.goodbyes == 1


def test_empty_input_does_not_call_provider() -> None:
    provider = FakeProvider([])
    ui = FakeTerminal(["   ", "/exit"])
    runner = asyncio.Runner()
    try:
        assert OkCodeApp(ui, _conversation(provider), runner, _config()).run() == 0
    finally:
        runner.close()
    assert provider.requests == []


def test_provider_error_returns_to_prompt() -> None:
    provider = FakeProvider([ProviderError(ProviderErrorKind.BAD_REQUEST, "请求错误")])
    ui = FakeTerminal(["问题", "/exit"])
    runner = asyncio.Runner()
    try:
        assert OkCodeApp(ui, _conversation(provider), runner, _config()).run() == 0
    finally:
        runner.close()
    assert len(ui.errors) == 1
    assert ui.goodbyes == 1


def test_unexpected_turn_error_is_reported_without_exiting_app() -> None:
    provider = FakeProvider([ValueError("运行期异常")])
    ui = FakeTerminal(["问题", "/exit"])
    runner = asyncio.Runner()
    try:
        assert OkCodeApp(ui, _conversation(provider), runner, _config()).run() == 0
    finally:
        runner.close()

    assert len(ui.runtime_errors) == 1
    assert isinstance(ui.runtime_errors[0], ValueError)
    assert ui.goodbyes == 1


def test_app_only_renders_events_not_internal_prompt_data() -> None:
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "完成"))])
    ui = FakeTerminal(["执行任务", "/exit"])
    runner = asyncio.Runner()
    try:
        assert OkCodeApp(ui, _conversation(provider), runner, _config()).run() == 0
    finally:
        runner.close()

    rendered = repr(ui.deltas)
    assert "okcode-system-note" not in rendered
    assert "## 身份" not in rendered
    assert "secret" not in rendered


def test_resume_selected_session_replaces_new_history_before_next_turn(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, clock=lambda: datetime(2026, 7, 30, 10, tzinfo=UTC))
    journal = store.create_journal()
    journal.append((ChatMessage(Role.USER, "旧问题"), ChatMessage(Role.ASSISTANT, "旧回答")))
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "继续回答"))])
    ui = FakeTerminal(["/resume", "继续任务", "/exit"], [journal.session_id])
    runner = asyncio.Runner()
    try:
        assert (
            OkCodeApp(ui, _conversation(provider, session_store=store), runner, _config()).run()
            == 0
        )
    finally:
        runner.close()

    assert len(ui.resumable_sessions) == 1
    assert [message.content for message in provider.requests[0]] == ["旧问题", "旧回答", "继续任务"]


def test_resume_cancellation_keeps_current_new_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, clock=lambda: datetime(2026, 7, 30, 10, tzinfo=UTC))
    journal = store.create_journal()
    journal.append((ChatMessage(Role.USER, "旧问题"), ChatMessage(Role.ASSISTANT, "旧回答")))
    provider = FakeProvider([StreamCompleted(ChatMessage(Role.ASSISTANT, "新回答"))])
    ui = FakeTerminal(["/resume", "新问题", "/exit"], [None])
    runner = asyncio.Runner()
    try:
        assert (
            OkCodeApp(ui, _conversation(provider, session_store=store), runner, _config()).run()
            == 0
        )
    finally:
        runner.close()

    assert [message.content for message in provider.requests[0]] == ["新问题"]


class InterruptRunner:
    def run(self, coroutine: object) -> object:
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()
        raise KeyboardInterrupt


class ExitRunner:
    def run(self, coroutine: object) -> object:
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()
        raise ExitRequested


def test_keyboard_interrupt_shows_cancelled_and_returns_to_prompt() -> None:
    provider = FakeProvider([])
    ui = FakeTerminal(["问题", "/exit"])
    app = OkCodeApp(ui, _conversation(provider), InterruptRunner(), _config())  # type: ignore[arg-type]
    assert app.run() == 0
    assert ui.cancelled == 1
    assert ui.goodbyes == 1


def test_exit_from_permission_prompt_exits_the_app() -> None:
    provider = FakeProvider([])
    ui = FakeTerminal(["问题"])
    app = OkCodeApp(ui, _conversation(provider), ExitRunner(), _config())  # type: ignore[arg-type]

    assert app.run() == 0
    assert ui.cancelled == 0
    assert ui.goodbyes == 1
