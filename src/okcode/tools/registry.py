"""工具注册与发现。"""

from okcode.tools.base import Tool
from okcode.tools.models import ToolDefinition, ToolSafety


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

    def replace(self, tool: Tool) -> None:
        """用同名工具的新定义替换已注册实例，供按需热更新能力包使用。"""

        definition = tool.definition
        if definition.name not in self._tools:
            raise ValueError(f"不能替换不存在的工具：{definition.name}")
        if definition.timeout_seconds <= 0:
            raise ValueError(f"工具超时必须为正数：{definition.name}")
        self._tools[definition.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """返回工具是否已注册。"""

        return name in self._tools

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition for name in sorted(self._tools))

    def definitions_by_names(
        self, names: tuple[str, ...] | list[str] | set[str]
    ) -> tuple[ToolDefinition, ...]:
        """按工具名返回模型可见定义，缺失时报错。"""

        missing = sorted(name for name in names if name not in self._tools)
        if missing:
            raise ValueError(f"工具不存在：{', '.join(missing)}")
        return tuple(self._tools[name].definition for name in sorted(set(names)))

    def definitions_by_safety(self, safety: ToolSafety) -> tuple[ToolDefinition, ...]:
        """按安全类别返回模型可见工具声明。"""

        return tuple(
            self._tools[name].definition
            for name in sorted(self._tools)
            if self._tools[name].definition.safety is safety
        )
