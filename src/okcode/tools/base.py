"""工具抽象接口。"""

from collections.abc import Mapping
from typing import Protocol

from okcode.tools.models import JSONValue, ToolDefinition, ToolOutput


class Tool(Protocol):
    """所有本地工具必须实现的最小接口。"""

    @property
    def definition(self) -> ToolDefinition:
        """返回模型可见的工具元信息。"""

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        """执行已通过 Schema 校验的工具参数。"""
