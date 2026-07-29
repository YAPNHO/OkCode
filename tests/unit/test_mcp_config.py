from __future__ import annotations

from pathlib import Path

import pytest

from okcode.errors import ConfigError
from okcode.mcp.config import load_mcp_config, stdio_environment
from okcode.mcp.models import McpConfigPaths, StdioMcpServerConfig, StreamableHttpMcpServerConfig


def _paths(tmp_path: Path) -> McpConfigPaths:
    return McpConfigPaths(
        user=tmp_path / "user" / ".okcode" / "config.yaml",
        project=tmp_path / "project" / ".okcode" / "config.yaml",
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_merges_servers_and_project_overrides_same_name(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write(
        paths.user,
        """
mcp_servers:
  shared:
    transport: stdio
    command: user-command
  user_only:
    transport: stdio
    command: user-only
""",
    )
    _write(
        paths.project,
        """
mcp_servers:
  shared:
    transport: streamable_http
    url: https://project.example/mcp
  project_only:
    transport: stdio
    command: project-only
""",
    )

    config = load_mcp_config(paths)

    assert [server.name for server in config.servers] == [
        "project_only",
        "shared",
        "user_only",
    ]
    shared = next(server for server in config.servers if server.name == "shared")
    assert isinstance(shared, StreamableHttpMcpServerConfig)
    assert shared.url == "https://project.example/mcp"


def test_missing_files_are_an_empty_configuration(tmp_path: Path) -> None:
    assert load_mcp_config(_paths(tmp_path)).servers == ()


def test_expands_declared_values_without_leaking_secret_on_error(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write(
        paths.user,
        """
mcp_servers:
  http:
    transport: streamable_http
    url: https://${HOST}/mcp
    headers:
      Authorization: Bearer ${TOKEN}
""",
    )
    config = load_mcp_config(paths, environ={"HOST": "api.example", "TOKEN": "test-secret"})
    server = config.servers[0]
    assert isinstance(server, StreamableHttpMcpServerConfig)
    assert server.url == "https://api.example/mcp"
    assert server.headers == {"Authorization": "Bearer test-secret"}

    with pytest.raises(ConfigError) as error:
        load_mcp_config(paths, environ={"HOST": "api.example"})
    message = str(error.value)
    assert "TOKEN" in message
    assert "test-secret" not in message
    assert "Bearer" not in message


@pytest.mark.parametrize(
    "content, expected",
    [
        (
            """
mcp_servers:
  bad:
    transport: stdio
    command: run
    url: https://example.test
""",
            "未知字段",
        ),
        (
            """
mcp_servers:
  bad:
    transport: streamable_http
    url: not-a-url
""",
            r"HTTP\(S\)",
        ),
        (
            """
mcp_servers:
  bad:
    transport: stdio
    command: run
    args: wrong
""",
            "字符串列表",
        ),
    ],
)
def test_rejects_invalid_server_fields(tmp_path: Path, content: str, expected: str) -> None:
    paths = _paths(tmp_path)
    _write(paths.user, content)
    with pytest.raises(ConfigError, match=expected):
        load_mcp_config(paths)


def test_stdio_environment_inherits_and_overrides() -> None:
    config = StdioMcpServerConfig("server", "command", env={"PATH": "custom", "TOKEN": "value"})
    assert stdio_environment(config, environ={"PATH": "base", "SYSTEM": "kept"}) == {
        "PATH": "custom",
        "SYSTEM": "kept",
        "TOKEN": "value",
    }
