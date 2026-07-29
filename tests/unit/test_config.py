from __future__ import annotations

from pathlib import Path

import pytest

from okcode.config import default_config_path, load_config
from okcode.errors import ConfigError


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_active_and_deepseek_thinking(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
active: deepseek
providers:
  - name: deepseek
    protocol: openai
    model: deepseek-v4-pro
    base_url: https://api.deepseek.com
    api_key: OKCODE_SECRET_DO_NOT_PRINT_7429
    thinking: true
  - name: claude
    protocol: anthropic
    model: claude-test
    base_url: https://api.anthropic.com
    api_key: another-key
""",
    )
    config = load_config(path)
    assert config.active_provider.name == "deepseek"
    assert config.active_provider.thinking is True
    assert "OKCODE_SECRET_DO_NOT_PRINT_7429" not in repr(config.active_provider)
    assert config.providers[1].thinking is False


@pytest.mark.parametrize(
    "content, message",
    [
        ("", "根节点"),
        ("active: x\nproviders: []", "非空列表"),
        ("active: x\nproviders: 1", "非空列表"),
        ("extra: true\nactive: x\nproviders: []", "未知字段"),
        (
            """active: x
providers:
  - name: x
    protocol: bad
    model: m
    base_url: https://x
    api_key: k""",
            "protocol",
        ),
        (
            """active: x
providers:
  - name: x
    protocol: openai
    model: m
    base_url: nope
    api_key: k""",
            "base_url",
        ),
        (
            '''active: x
providers:
  - name: x
    protocol: openai
    model: m
    base_url: https://x
    api_key: k
    thinking: "yes"''',
            "thinking",
        ),
    ],
)
def test_invalid_config(content: str, message: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, content))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="未找到"):
        load_config(tmp_path / "missing.yaml")


def test_yaml_syntax_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML 语法错误"):
        load_config(_write(tmp_path, "active: ["))


def test_duplicate_and_invalid_active(tmp_path: Path) -> None:
    duplicate = """
active: one
providers:
  - {name: one, protocol: openai, model: m, base_url: https://a, api_key: k}
  - {name: one, protocol: openai, model: m, base_url: https://b, api_key: k}
"""
    with pytest.raises(ConfigError, match="不能重复"):
        load_config(_write(tmp_path, duplicate))

    invalid_active = duplicate.replace(
        "name: one, protocol: openai, model: m, base_url: https://b, api_key: k",
        "name: two, protocol: openai, model: m, base_url: https://b, api_key: k",
    ).replace("active: one", "active: unknown")
    with pytest.raises(ConfigError, match="不存在"):
        load_config(_write(tmp_path, invalid_active))


def test_default_path_uses_current_working_directory() -> None:
    assert default_config_path() == Path.cwd() / "config.yaml"


def test_prompt_cache_is_optional_boolean(tmp_path: Path) -> None:
    enabled = _write(
        tmp_path,
        """
active: x
providers:
  - name: x
    protocol: openai
    model: test
    base_url: https://x.example
    api_key: secret
    prompt_cache: true
""",
    )
    assert load_config(enabled).active_provider.prompt_cache is True

    invalid = enabled.with_name("invalid.yaml")
    invalid.write_text(
        enabled.read_text(encoding="utf-8").replace("true", '"yes"'), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="prompt_cache"):
        load_config(invalid)
