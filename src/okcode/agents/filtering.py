"""子 Agent 可见工具过滤。"""

from __future__ import annotations

from dataclasses import dataclass

from okcode.agents.models import AgentToolPolicy
from okcode.tools.base import Tool
from okcode.tools.models import ToolDefinition, ToolSafety
from okcode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ToolFilterResult:
    """工具过滤后的 registry 和拒绝原因。"""

    registry: FilteredToolRegistry
    denied_reasons: dict[str, str]


class FilteredToolRegistry:
    """只暴露子 Agent 可见工具的 registry 视图。"""

    def __init__(
        self,
        parent: ToolRegistry,
        visible_tool_names: set[str] | tuple[str, ...],
        denied_reasons: dict[str, str] | None = None,
    ) -> None:
        self._parent = parent
        self._visible_tool_names = set(visible_tool_names)
        self.denied_reasons = dict(denied_reasons or {})

    @property
    def visible_tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._visible_tool_names))

    def get(self, name: str) -> Tool | None:
        if name not in self._visible_tool_names:
            return None
        return self._parent.get(name)

    def has(self, name: str) -> bool:
        return name in self._visible_tool_names and self._parent.has(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            definition
            for definition in self._parent.definitions()
            if definition.name in self._visible_tool_names
        )

    def definitions_by_names(
        self, names: tuple[str, ...] | list[str] | set[str]
    ) -> tuple[ToolDefinition, ...]:
        missing = sorted(name for name in names if name not in self._visible_tool_names)
        if missing:
            raise ValueError(f"工具对子 Agent 不可见：{', '.join(missing)}")
        return self._parent.definitions_by_names(names)

    def definitions_by_safety(self, safety: ToolSafety) -> tuple[ToolDefinition, ...]:
        return tuple(
            definition
            for definition in self._parent.definitions_by_safety(safety)
            if definition.name in self._visible_tool_names
        )


def filter_agent_tools(parent: ToolRegistry, policy: AgentToolPolicy) -> ToolFilterResult:
    """按安全边界和角色约束计算子 Agent 可见工具。"""

    visible = {definition.name for definition in parent.definitions()}
    denied: dict[str, str] = {}

    def deny(name: str, reason: str) -> None:
        if name in visible:
            visible.remove(name)
        denied.setdefault(name, reason)

    for name in sorted(policy.global_denied):
        deny(name, "全局禁止")

    if policy.background_allowed is not None:
        allowed = set(policy.background_allowed)
        for name in sorted(visible - allowed):
            deny(name, "不在后台白名单中")

    if policy.parent_allowed is not None:
        allowed = set(policy.parent_allowed)
        for name in sorted(visible - allowed):
            deny(name, "父 Agent 当前不可见")

    if policy.role_allowlist:
        allowed = set(policy.role_allowlist)
        for name in sorted(visible - allowed):
            deny(name, "不在角色白名单中")

    for name in sorted(policy.role_denylist):
        deny(name, "角色黑名单禁止")

    if policy.depth >= policy.max_depth:
        deny("agent", "达到子 Agent 嵌套深度上限")

    return ToolFilterResult(FilteredToolRegistry(parent, visible, denied), denied)
