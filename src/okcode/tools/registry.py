"""工具注册与发现。"""

from okcode.tools.base import Tool
from okcode.tools.models import ToolDefinition


class ToolRegistry:
    """集中管理当前会话可用工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        definition = tool.definition
        if not definition.name:
            raise ValueError("工具名称不能为空。")
        if definition.name in self._tools:
            raise ValueError(f"工具名称重复：{definition.name}")
        if definition.timeout_seconds <= 0:
            raise ValueError(f"工具超时必须为正数：{definition.name}")
        self._tools[definition.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition for name in sorted(self._tools))
