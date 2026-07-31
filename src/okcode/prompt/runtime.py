"""普通请求的运行时提示词上下文来源。"""

from __future__ import annotations

import platform as host_platform
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from okcode.prompt.builder import PromptBuildContext, PromptOptionalSections
from okcode.prompt.modes import TurnKind
from okcode.tools.models import ToolDefinition

if TYPE_CHECKING:
    from okcode.memory.store import MemoryStore


class RuntimePromptContextFactory:
    """将固定项目指令和最新记忆索引装配到每一轮请求。"""

    def __init__(
        self,
        workspace_root: Path,
        custom_instructions: str,
        memory_store: MemoryStore,
        *,
        available_skills_provider: Callable[[], str] | None = None,
        active_skills_provider: Callable[[], str] | None = None,
        current_date: Callable[[], date] | None = None,
        platform_name: Callable[[], str] | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._custom_instructions = custom_instructions
        self._memory_store = memory_store
        self._available_skills_provider = available_skills_provider or (lambda: "")
        self._active_skills_provider = active_skills_provider or (lambda: "")
        self._current_date = current_date or date.today
        self._platform_name = platform_name or host_platform.platform

    def __call__(
        self,
        turn_kind: TurnKind,
        iteration: int,
        tools: Sequence[ToolDefinition],
    ) -> PromptBuildContext:
        """读取刚更新的索引并构建本轮动态提示词上下文。"""

        return PromptBuildContext(
            workspace_root=str(self._workspace_root),
            platform=self._platform_name(),
            current_date=self._current_date().isoformat(),
            available_tool_names=tuple(tool.name for tool in tools),
            turn_kind=turn_kind,
            iteration=iteration,
            optional_sections=PromptOptionalSections(
                custom_instructions=self._custom_instructions,
                available_skills=self._available_skills_provider(),
                active_skills=self._active_skills_provider(),
                long_term_memory=self._memory_store.read_context(),
            ),
        )
