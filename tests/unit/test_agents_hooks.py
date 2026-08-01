from __future__ import annotations

from okcode.agents.models import (
    AgentLaunchKind,
    AgentTaskSnapshot,
    AgentTaskStatus,
    ParentAgentContext,
)
from okcode.commands.models import RuntimeMode
from okcode.hooks.actions import HookActionRunner
from okcode.hooks.models import (
    HookContext,
    HookControl,
    HookEvent,
    HookRule,
    SubAgentHookAction,
)
from okcode.permissions.models import PermissionMode
from okcode.tools.workspace import Workspace


class Launcher:
    def __init__(self) -> None:
        self.calls = []

    def launch_from_hook(self, action, context, parent):
        self.calls.append((action, context, parent))
        return AgentTaskSnapshot("task-1", AgentLaunchKind.DEFINED, AgentTaskStatus.BACKGROUND)


def _rule() -> HookRule:
    return HookRule(
        "sub",
        HookEvent.TOOL_AFTER,
        None,
        SubAgentHookAction("整理结果", "researcher"),
        HookControl(background=True),
    )


def _parent() -> ParentAgentContext:
    return ParentAgentContext("parent", (), RuntimeMode.DEFAULT, PermissionMode.DEFAULT, ())


async def test_hook_subagent_starts_real_background_task(tmp_path) -> None:
    launcher = Launcher()
    runner = HookActionRunner(
        Workspace(tmp_path),
        agent_launcher=launcher,  # type: ignore[arg-type]
        parent_context_provider=_parent,
    )

    outcome = await runner.run(_rule(), HookContext(HookEvent.TOOL_AFTER, {}))

    assert outcome.status == "subagent_started"
    assert "task-1" in outcome.message
    assert launcher.calls[0][0].profile == "researcher"


async def test_hook_subagent_keeps_placeholder_without_launcher(tmp_path) -> None:
    runner = HookActionRunner(Workspace(tmp_path))

    outcome = await runner.run(_rule(), HookContext(HookEvent.TOOL_AFTER, {}))

    assert outcome.status == "subagent_skipped"
