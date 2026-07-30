from __future__ import annotations

from datetime import date
from pathlib import Path

from okcode.memory.models import (
    MemoryAction,
    MemoryCategory,
    MemoryIndexEntry,
    MemoryOperation,
    MemoryPaths,
    MemoryScope,
    MemoryUpdate,
)
from okcode.memory.store import MemoryStore
from okcode.prompt import RuntimePromptContextFactory, TurnKind
from okcode.tools.models import ToolDefinition, ToolSafety


def _tool() -> ToolDefinition:
    return ToolDefinition(
        name="read_file",
        description="读取文件",
        input_schema={"type": "object", "additionalProperties": False},
        timeout_seconds=5,
        safety=ToolSafety.READ_ONLY,
    )


def test_runtime_context_loads_fixed_instructions_and_latest_memory(tmp_path: Path) -> None:
    paths = MemoryPaths(tmp_path / "memory", tmp_path / "user-memory")
    store = MemoryStore(paths)
    store.apply(
        MemoryUpdate(
            (
                MemoryOperation(
                    MemoryScope.PROJECT,
                    MemoryCategory.PROJECT_KNOWLEDGE,
                    MemoryAction.CREATE,
                    "architecture",
                    "架构",
                    "项目使用分层架构。",
                ),
            ),
            (),
            (
                MemoryIndexEntry(
                    "architecture",
                    MemoryCategory.PROJECT_KNOWLEDGE,
                    "分层架构",
                ),
            ),
        )
    )
    factory = RuntimePromptContextFactory(
        tmp_path,
        "项目根指令\n\n用户指令",
        store,
        current_date=lambda: date(2026, 7, 30),
        platform_name=lambda: "Windows",
    )

    context = factory(TurnKind.NORMAL, 2, (_tool(),))

    assert context.workspace_root == str(tmp_path)
    assert context.current_date == "2026-07-30"
    assert context.optional_sections.custom_instructions.startswith("项目根指令")
    assert "项目级长期记忆" in context.optional_sections.long_term_memory
    assert "分层架构" in context.optional_sections.long_term_memory
    assert context.available_tool_names == ("read_file",)
