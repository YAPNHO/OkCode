"""coordinator 模式双锁、工具过滤和命令保护。"""

from __future__ import annotations

from collections.abc import Mapping

from okcode.models import AppConfig
from okcode.prompt import SystemInstruction
from okcode.tools.base import Tool
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.registry import ToolRegistry

COORDINATOR_ENV = "OKCODE_COORDINATOR"
TEAM_TOOL_NAMES = {"team_task", "team_message", "team_member", "team_merge"}
WRITE_TOOL_NAMES = {"write_file", "edit_file"}


class CoordinatorPolicy:
    """判断和应用 coordinator 模式。"""

    def is_enabled(self, config: AppConfig, environ: Mapping[str, str]) -> bool:
        return bool(config.team.coordinator_enabled and environ.get(COORDINATOR_ENV) == "1")

    def filter_tool_names(self, registry: ToolRegistry) -> tuple[str, ...]:
        names = []
        for definition in registry.definitions():
            if definition.name in TEAM_TOOL_NAMES or definition.name == "run_command":
                names.append(definition.name)
            elif definition.safety is ToolSafety.READ_ONLY:
                names.append(definition.name)
        return tuple(sorted(names))

    def build_instruction(self) -> SystemInstruction:
        return SystemInstruction(
            "team_coordinator",
            (
                "当前处于 coordinator 模式。你的职责是拆解任务、派人成员、"
                "审批计划、跟踪消息、终止成员和合并代码；不要直接编辑业务文件。"
            ),
            priority=84,
        )


class CoordinatorCommandGuard:
    """限制 coordinator 模式下明显写文件的 shell 命令。"""

    _blocked_fragments = (
        ">",
        "set-content",
        "out-file",
        "remove-item",
        "move-item",
        "copy-item",
        "apply_patch",
    )

    def validate(self, command: str) -> None:
        lowered = command.lower()
        allowed_git = (
            lowered.startswith("git status")
            or lowered.startswith("git diff")
            or lowered.startswith("git merge")
            or lowered.startswith("git rev-parse")
            or lowered.startswith("git branch")
        )
        if allowed_git:
            return
        for fragment in self._blocked_fragments:
            if fragment in lowered:
                raise ToolFailure(
                    ToolErrorCode.PERMISSION_DENIED,
                    "coordinator 模式禁止通过 shell 直接写文件；请派成员执行或使用团队合并流程。",
                    {"blocked_command": command},
                )


class GuardedRunCommandTool:
    """为 coordinator 模式包装 run_command。"""

    def __init__(self, inner: Tool, guard: CoordinatorCommandGuard | None = None) -> None:
        self._inner = inner
        self._guard = guard or CoordinatorCommandGuard()

    @property
    def definition(self) -> ToolDefinition:
        return self._inner.definition

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        command = str(arguments.get("command", ""))
        self._guard.validate(command)
        return await self._inner.execute(arguments)
