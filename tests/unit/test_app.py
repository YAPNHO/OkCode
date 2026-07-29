from __future__ import annotations

import asyncio

from okcode.app import OkCodeApp
from okcode.conversation import ConversationSession
from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    AgentProgress,
    ChatMessage,
    ProviderConfig,
    ProviderProtocol,
    Role,
    StreamCompleted,
    TextDelta,
)
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


def _conversation(provider: FakeProvider) -> ConversationSession:
    registry = ToolRegistry()
    return ConversationSession(provider, registry, ToolExecutor(registry))


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
    assert ui.deltas[0] == AgentProgress("模型迭代 1/12", 1)
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


class InterruptRunner:
    def run(self, coroutine: object) -> object:
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()
        raise KeyboardInterrupt


def test_keyboard_interrupt_shows_cancelled_and_returns_to_prompt() -> None:
    provider = FakeProvider([])
    ui = FakeTerminal(["问题", "/exit"])
    app = OkCodeApp(ui, _conversation(provider), InterruptRunner(), _config())  # type: ignore[arg-type]
    assert app.run() == 0
    assert ui.cancelled == 1
    assert ui.goodbyes == 1
