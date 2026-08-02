"""默认核心工具的装配。"""

from okcode.tools.command import RunCommandTool
from okcode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from okcode.tools.registry import ToolRegistry
from okcode.tools.search import FindFilesTool, SearchCodeTool
from okcode.tools.workspace import Workspace


def build_default_registry(workspace: Workspace) -> ToolRegistry:
    """创建固定的六项核心工具注册表。"""

    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)
    return registry


def register_workspace_tools(registry: ToolRegistry, workspace: Workspace) -> None:
    """向注册表加入绑定到指定工作区的本地文件/命令工具。"""

    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    registry.register(EditFileTool(workspace))
    registry.register(RunCommandTool(workspace))
    registry.register(FindFilesTool(workspace))
    registry.register(SearchCodeTool(workspace))


def build_child_registry(parent: ToolRegistry, workspace: Workspace) -> ToolRegistry:
    """为子 Agent 重建本地工具，并复用 MCP/Skill 等非本地工具。"""

    registry = build_default_registry(workspace)
    local_tool_names = {definition.name for definition in registry.definitions()}
    for definition in parent.definitions():
        if definition.name in local_tool_names:
            continue
        tool = parent.get(definition.name)
        if tool is not None:
            registry.register(tool)
    return registry


def build_team_registry(
    parent: ToolRegistry,
    *,
    runtime: object | None = None,
    context: object | None = None,
) -> ToolRegistry:
    """在父 registry 基础上按团队上下文注入团队工具。"""

    from okcode.teams.tools import register_team_tools

    registry = ToolRegistry()
    for definition in parent.definitions():
        tool = parent.get(definition.name)
        if tool is not None:
            registry.register(tool)
    register_team_tools(registry, runtime, context)
    return registry
