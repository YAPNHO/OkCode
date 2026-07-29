from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from okcode.tools.defaults import build_default_registry
from okcode.tools.models import JSONValue, ToolDefinition, ToolOutput, ToolSafety
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace


class FakeTool:
    def __init__(self, name: str, *, timeout_seconds: float = 1) -> None:
        self._definition = ToolDefinition(
            name=name,
            description="测试工具",
            input_schema={"type": "object", "additionalProperties": False},
            timeout_seconds=timeout_seconds,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        return ToolOutput("成功")


def test_registry_registers_and_sorts_definitions() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool("zeta"))
    registry.register(FakeTool("alpha"))

    assert registry.get("alpha") is not None
    assert registry.get("missing") is None
    assert [definition.name for definition in registry.definitions()] == ["alpha", "zeta"]


def test_registry_rejects_duplicate_empty_and_invalid_timeout() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool("same"))
    with pytest.raises(ValueError, match="重复"):
        registry.register(FakeTool("same"))
    with pytest.raises(ValueError, match="不能为空"):
        registry.register(FakeTool(""))
    with pytest.raises(ValueError, match="超时"):
        registry.register(FakeTool("slow", timeout_seconds=0))


def test_default_registry_contains_exactly_six_tools(tmp_path: Path) -> None:
    registry = build_default_registry(Workspace(tmp_path))
    definitions = registry.definitions()

    assert [definition.name for definition in definitions] == [
        "edit_file",
        "find_files",
        "read_file",
        "run_command",
        "search_code",
        "write_file",
    ]
    assert all(
        definition.description and definition.timeout_seconds > 0 for definition in definitions
    )
    assert all(definition.input_schema["type"] == "object" for definition in definitions)
    assert {definition.name: definition.safety for definition in definitions} == {
        "edit_file": ToolSafety.SIDE_EFFECT,
        "find_files": ToolSafety.READ_ONLY,
        "read_file": ToolSafety.READ_ONLY,
        "run_command": ToolSafety.SIDE_EFFECT,
        "search_code": ToolSafety.READ_ONLY,
        "write_file": ToolSafety.SIDE_EFFECT,
    }
    read_only_names = [
        definition.name for definition in registry.definitions_by_safety(ToolSafety.READ_ONLY)
    ]
    assert read_only_names == [
        "find_files",
        "read_file",
        "search_code",
    ]
