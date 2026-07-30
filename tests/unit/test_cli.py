from __future__ import annotations

from okcode import cli
from okcode.errors import ConfigError
from okcode.mcp.models import McpConfig, McpDiscoveryResult, McpDiscoveryWarning, McpRemoteToolInfo
from okcode.mcp.tool import McpRemoteTool
from okcode.models import AppConfig, ProviderConfig, ProviderProtocol
from okcode.permissions.models import PermissionConfirmation, PermissionRequest
from okcode.prompt import RuntimePromptContextFactory
from okcode.providers.factory import create_provider
from okcode.sessions import SessionStore
from okcode.tools.registry import ToolRegistry


class StubUI:
    def __init__(self) -> None:
        self.config_errors: list[str] = []
        self.mcp_warnings: list[McpDiscoveryWarning] = []
        self.startup_errors = 0

    def show_config_error(self, message: str) -> None:
        self.config_errors.append(message)

    def show_startup_error(self) -> None:
        self.startup_errors += 1

    def show_mcp_warning(self, warning: McpDiscoveryWarning) -> None:
        self.mcp_warnings.append(warning)

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


def test_invalid_mcp_config_does_not_create_provider(monkeypatch) -> None:
    ui = StubUI()
    called = False
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)
    monkeypatch.setattr(
        cli,
        "load_mcp_config",
        lambda _: (_ for _ in ()).throw(ConfigError("MCP 配置无效")),
    )

    def unexpected(_: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("MCP 配置无效时不应创建 Provider")

    monkeypatch.setattr(cli, "create_provider", unexpected)

    assert cli.main() == 2
    assert ui.config_errors == ["MCP 配置无效"]
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
    monkeypatch.setattr(cli, "load_mcp_config", lambda _: McpConfig())
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
    monkeypatch.setattr(cli, "load_mcp_config", lambda _: McpConfig())
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
    assert isinstance(conversation._session_store, SessionStore)  # type: ignore[attr-defined]
    assert conversation._session_journal is not None  # type: ignore[attr-defined]
    assert isinstance(conversation._context_factory, RuntimePromptContextFactory)  # type: ignore[attr-defined]
    assert conversation._memory_worker is not None  # type: ignore[attr-defined]


def test_main_closes_memory_worker_before_mcp_and_provider(monkeypatch, tmp_path) -> None:
    ui = StubUI()
    close_order: list[str] = []

    class Worker:
        def __init__(self, *_: object) -> None:
            pass

        def close(self) -> None:
            close_order.append("memory")

    class Manager:
        def __init__(self, _: object) -> None:
            pass

        async def discover_tools(self) -> McpDiscoveryResult:
            return McpDiscoveryResult(())

        async def aclose(self) -> None:
            close_order.append("mcp")

    class Provider:
        async def aclose(self) -> None:
            close_order.append("provider")

    class App:
        def __init__(self, *_: object) -> None:
            pass

        def run(self) -> int:
            return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)
    monkeypatch.setattr(cli, "load_mcp_config", lambda _: McpConfig())
    monkeypatch.setattr(cli, "MemoryWorker", Worker)
    monkeypatch.setattr(cli, "McpClientManager", Manager)
    monkeypatch.setattr(cli, "create_provider", lambda _: Provider())
    monkeypatch.setattr(cli, "OkCodeApp", App)

    assert cli.main() == 0
    assert close_order == ["memory", "mcp", "provider"]


def test_invalid_permission_rules_do_not_create_provider(monkeypatch, tmp_path) -> None:
    ui = StubUI()
    created = False
    permissions = tmp_path / ".okcode"
    permissions.mkdir()
    (permissions / "permissions.yaml").write_text("rules: invalid\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)
    monkeypatch.setattr(cli, "load_mcp_config", lambda _: McpConfig())

    def unexpected(_: object) -> object:
        nonlocal created
        created = True
        raise AssertionError("权限规则无效时不应创建 Provider")

    monkeypatch.setattr(cli, "create_provider", unexpected)

    assert cli.main() == 2
    assert created is False
    assert "permissions.yaml" in ui.config_errors[0]


def test_main_registers_discovered_mcp_tools_before_loading_permissions(
    monkeypatch,
    tmp_path,
) -> None:
    ui = StubUI()
    observed: dict[str, object] = {}
    close_order: list[str] = []

    class Caller:
        async def call_tool(self, *_: object, **__: object) -> object:
            raise AssertionError("测试不应调用远端工具")

    tool = McpRemoteTool(
        McpRemoteToolInfo(
            "server",
            "echo",
            "回显文本。",
            {"type": "object", "additionalProperties": False},
        ),
        Caller(),  # type: ignore[arg-type]
    )

    class Manager:
        closed = False

        def __init__(self, _: object) -> None:
            pass

        async def discover_tools(self) -> McpDiscoveryResult:
            return McpDiscoveryResult(
                (tool,),
                (McpDiscoveryWarning("bad", "初始化", "MCP Server 在初始化阶段失败。"),),
            )

        async def aclose(self) -> None:
            self.closed = True
            close_order.append("mcp")

    manager = Manager(None)

    class Provider:
        async def aclose(self) -> None:
            observed["provider_closed"] = True
            close_order.append("provider")

    class App:
        def __init__(self, _ui: object, conversation: object, *_: object) -> None:
            observed["tools"] = [
                definition.name
                for definition in conversation._registry.definitions()  # type: ignore[attr-defined]
            ]

        def run(self) -> int:
            return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)
    monkeypatch.setattr(cli, "load_mcp_config", lambda _: McpConfig())
    monkeypatch.setattr(cli, "McpClientManager", lambda _: manager)
    monkeypatch.setattr(cli, "create_provider", lambda _: Provider())
    monkeypatch.setattr(cli, "OkCodeApp", App)

    assert cli.main() == 0
    assert "mcp__server__echo" in observed["tools"]
    assert ui.mcp_warnings[0].server_name == "bad"
    assert manager.closed is True
    assert observed["provider_closed"] is True
    assert close_order == ["mcp", "provider"]


def test_main_skips_mcp_tool_that_conflicts_with_existing_registry_entry(
    monkeypatch, tmp_path
) -> None:
    ui = StubUI()

    class Caller:
        async def call_tool(self, *_: object, **__: object) -> object:
            raise AssertionError("测试不应调用远端工具")

    tool = McpRemoteTool(
        McpRemoteToolInfo(
            "server",
            "echo",
            "回显文本。",
            {"type": "object", "additionalProperties": False},
        ),
        Caller(),  # type: ignore[arg-type]
    )
    registry = ToolRegistry()
    registry.register(tool)

    class Manager:
        async def discover_tools(self) -> McpDiscoveryResult:
            return McpDiscoveryResult((tool,))

        async def aclose(self) -> None:
            pass

    class Provider:
        async def aclose(self) -> None:
            pass

    class App:
        def __init__(self, *_: object) -> None:
            pass

        def run(self) -> int:
            return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "TerminalUI", lambda: ui)
    monkeypatch.setattr(cli, "load_config", _config)
    monkeypatch.setattr(cli, "load_mcp_config", lambda _: McpConfig())
    monkeypatch.setattr(cli, "build_default_registry", lambda _: registry)
    monkeypatch.setattr(cli, "McpClientManager", lambda _: Manager())
    monkeypatch.setattr(cli, "create_provider", lambda _: Provider())
    monkeypatch.setattr(cli, "OkCodeApp", App)

    assert cli.main() == 0
    assert [definition.name for definition in registry.definitions()] == ["mcp__server__echo"]
    assert ui.mcp_warnings[0].server_name == "server"
    assert ui.mcp_warnings[0].phase == "工具注册"
