from __future__ import annotations

from okcode import cli
from okcode.errors import ConfigError
from okcode.models import AppConfig, ProviderConfig, ProviderProtocol
from okcode.permissions.models import PermissionConfirmation, PermissionRequest
from okcode.providers.factory import create_provider


class StubUI:
    def __init__(self) -> None:
        self.config_errors: list[str] = []
        self.startup_errors = 0

    def show_config_error(self, message: str) -> None:
        self.config_errors.append(message)

    def show_startup_error(self) -> None:
        self.startup_errors += 1

    def confirm_permission(self, _: PermissionRequest) -> PermissionConfirmation:
        return PermissionConfirmation.DENY


def _config() -> AppConfig:
    provider = ProviderConfig(
        name="test",
        protocol=ProviderProtocol.OPENAI,
        model="m",
        base_url="https://example.test",
        api_key="secret",
    )
    return AppConfig(active="test", providers=(provider,))


def test_invalid_config_does_not_create_provider(monkeypatch) -> None:
    ui = StubUI()
    called = False
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", lambda: (_ for _ in ()).throw(ConfigError("坏配置")))

    def unexpected(_: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("不应创建 Provider")

    monkeypatch.setattr(cli, "create_provider", unexpected)
    assert cli.main() == 2
    assert ui.config_errors == ["坏配置"]
    assert called is False


def test_factory_creates_both_provider_types() -> None:
    openai = ProviderConfig("o", ProviderProtocol.OPENAI, "m", "https://x", "k")
    anthropic = ProviderConfig("a", ProviderProtocol.ANTHROPIC, "m", "https://x", "k")
    assert type(create_provider(openai)).__name__ == "OpenAIProvider"
    assert type(create_provider(anthropic)).__name__ == "AnthropicProvider"


def test_main_closes_provider(monkeypatch) -> None:
    ui = StubUI()
    closed = 0

    class Provider:
        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    class App:
        def __init__(self, *_: object) -> None:
            pass

        def run(self) -> int:
            return 0

    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)
    monkeypatch.setattr(cli, "create_provider", lambda _: Provider())
    monkeypatch.setattr(cli, "OkCodeApp", App)
    assert cli.main() == 0
    assert closed == 1


def test_main_builds_default_tool_system_from_current_directory(monkeypatch, tmp_path) -> None:
    ui = StubUI()
    observed: dict[str, object] = {}

    class Provider:
        async def aclose(self) -> None:
            pass

    class App:
        def __init__(self, _ui: object, conversation: object, *_: object) -> None:
            observed["conversation"] = conversation

        def run(self) -> int:
            return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)
    monkeypatch.setattr(cli, "create_provider", lambda _: Provider())
    monkeypatch.setattr(cli, "OkCodeApp", App)

    assert cli.main() == 0
    conversation = observed["conversation"]
    assert [definition.name for definition in conversation._registry.definitions()] == [  # type: ignore[attr-defined]
        "edit_file",
        "find_files",
        "read_file",
        "run_command",
        "search_code",
        "write_file",
    ]


def test_invalid_permission_rules_do_not_create_provider(monkeypatch, tmp_path) -> None:
    ui = StubUI()
    created = False
    permissions = tmp_path / ".okcode"
    permissions.mkdir()
    (permissions / "permissions.yaml").write_text("rules: invalid\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)

    def unexpected(_: object) -> object:
        nonlocal created
        created = True
        raise AssertionError("权限规则无效时不应创建 Provider")

    monkeypatch.setattr(cli, "create_provider", unexpected)

    assert cli.main() == 2
    assert created is False
    assert "permissions.yaml" in ui.config_errors[0]
