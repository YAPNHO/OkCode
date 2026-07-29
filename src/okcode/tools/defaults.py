"""默认核心工具的装配。"""

from okcode.tools.command import RunCommandTool
from okcode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from okcode.tools.registry import ToolRegistry
from okcode.tools.search import FindFilesTool, SearchCodeTool
from okcode.tools.workspace import Workspace


def build_default_registry(workspace: Workspace) -> ToolRegistry:
    """创建固定的六项核心工具注册表。"""

    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    registry.register(EditFileTool(workspace))
    registry.register(RunCommandTool(workspace))
    registry.register(FindFilesTool(workspace))
    registry.register(SearchCodeTool(workspace))
    return registry
