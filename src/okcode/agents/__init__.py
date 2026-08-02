"""子 Agent 委派执行能力。"""

from typing import TYPE_CHECKING

from okcode.agents.filtering import FilteredToolRegistry, filter_agent_tools
from okcode.agents.launcher import AgentLauncher
from okcode.agents.manager import AgentTaskManager
from okcode.agents.models import (
    AgentExecutionMode,
    AgentIsolationMode,
    AgentLaunchKind,
    AgentModelKind,
    AgentModelPolicy,
    AgentPermissionKind,
    AgentPermissionPolicy,
    AgentRole,
    AgentRoleCatalog,
    AgentRoleListEntry,
    AgentRoleSourceKind,
    AgentTaskStatus,
    AgentToolPolicy,
    ParentAgentContext,
)
from okcode.agents.roles import AgentRolePaths, load_agent_roles
from okcode.agents.tool import AGENT_TOOL_NAME, AgentTool

if TYPE_CHECKING:
    from okcode.agents.runner import AgentRunner


def __getattr__(name: str) -> object:
    if name == "AgentRunner":
        from okcode.agents.runner import AgentRunner

        return AgentRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AgentExecutionMode",
    "AgentIsolationMode",
    "AgentLaunchKind",
    "AgentModelKind",
    "AgentModelPolicy",
    "AgentPermissionKind",
    "AgentPermissionPolicy",
    "ParentAgentContext",
    "AgentRole",
    "AgentRoleCatalog",
    "AgentRoleListEntry",
    "AgentRolePaths",
    "AgentRoleSourceKind",
    "AgentTaskStatus",
    "AgentToolPolicy",
    "AGENT_TOOL_NAME",
    "AgentTool",
    "AgentLauncher",
    "AgentRunner",
    "AgentTaskManager",
    "FilteredToolRegistry",
    "filter_agent_tools",
    "load_agent_roles",
]
