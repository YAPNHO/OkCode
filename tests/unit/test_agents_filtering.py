from __future__ import annotations

from collections.abc import Mapping

import pytest

from okcode.agents.filtering import filter_agent_tools
from okcode.agents.models import AgentToolPolicy
from okcode.tools.models import JSONValue, ToolDefinition, ToolOutput, ToolSafety
from okcode.tools.registry import ToolRegistry


class FakeTool:
    def __init__(self, name: str, safety: ToolSafety = ToolSafety.READ_ONLY) -> None:
        self._definition = ToolDefinition(
            name=name,
            description="测试工具",
            input_schema={"type": "object", "additionalProperties": False},
            timeout_seconds=1,
            safety=safety,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        return ToolOutput("ok")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FakeTool("agent", ToolSafety.SIDE_EFFECT))
    registry.register(FakeTool("read_file"))
    registry.register(FakeTool("search_code"))
    registry.register(FakeTool("write_file", ToolSafety.SIDE_EFFECT))
    return registry


def test_filter_agent_tools_denies_agent_by_default() -> None:
    result = filter_agent_tools(_registry(), AgentToolPolicy())

    assert result.registry.visible_tool_names == ("read_file", "search_code", "write_file")
    assert result.registry.get("agent") is None
    assert result.denied_reasons["agent"] in {"全局禁止", "达到子 Agent 嵌套深度上限"}


def test_filter_agent_tools_applies_background_parent_and_role_layers() -> None:
    result = filter_agent_tools(
        _registry(),
        AgentToolPolicy(
            background_allowed=("read_file", "search_code"),
            parent_allowed=("read_file", "write_file"),
            role_allowlist=("read_file", "search_code"),
            role_denylist=("search_code",),
        ),
    )

    assert result.registry.visible_tool_names == ("read_file",)
    assert result.denied_reasons["write_file"] == "不在后台白名单中"
    assert result.denied_reasons["search_code"] == "父 Agent 当前不可见"


def test_global_denied_wins_even_when_role_allows_tool() -> None:
    result = filter_agent_tools(
        _registry(),
        AgentToolPolicy(global_denied=("write_file",), role_allowlist=("write_file",)),
    )

    assert result.registry.visible_tool_names == ()
    assert result.denied_reasons["write_file"] == "全局禁止"


def test_empty_filter_result_still_returns_registry_and_diagnostics() -> None:
    result = filter_agent_tools(
        _registry(),
        AgentToolPolicy(role_allowlist=("missing",)),
    )

    assert result.registry.visible_tool_names == ()
    assert result.denied_reasons


def test_filtered_registry_rejects_definitions_for_invisible_tool() -> None:
    result = filter_agent_tools(_registry(), AgentToolPolicy(role_allowlist=("read_file",)))

    assert [item.name for item in result.registry.definitions()] == ["read_file"]
    assert [item.name for item in result.registry.definitions_by_safety(ToolSafety.READ_ONLY)] == [
        "read_file"
    ]
    with pytest.raises(ValueError, match="不可见"):
        result.registry.definitions_by_names(("write_file",))
