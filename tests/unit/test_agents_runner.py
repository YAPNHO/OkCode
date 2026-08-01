from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from okcode.agents.manager import AgentCancelToken
from okcode.agents.models import (
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentPermissionPolicy,
    AgentRole,
    AgentRoleSourceKind,
    AgentTaskStatus,
)
from okcode.agents.runner import AgentRunner
from okcode.models import ChatMessage, Role, StreamCompleted, TokenUsage, ToolCall
from okcode.permissions.manager import PermissionManager
from okcode.permissions.models import PermissionConfirmation, PermissionMode
from okcode.permissions.rules import PermissionPaths
from okcode.tools.models import JSONValue, ToolDefinition, ToolErrorCode, ToolOutput
from okcode.tools.registry import ToolRegistry
from okcode.tools.workspace import Workspace
from tests.fakes import FakeProvider


class EchoTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="读文件",
            input_schema={"type": "object", "additionalProperties": False},
            timeout_seconds=1,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        return ToolOutput("工具结果")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def _assistant(text: str) -> StreamCompleted:
    return StreamCompleted(ChatMessage(Role.ASSISTANT, content=text), TokenUsage(10, 5, 15, True))


def _role(tmp_path: Path) -> AgentRole:
    return AgentRole(
        name="reviewer",
        description="审查",
        source_kind=AgentRoleSourceKind.PROJECT,
        source_path=tmp_path / "reviewer.md",
        permission_policy=AgentPermissionPolicy(resolved_mode=PermissionMode.STRICT),
        system_prompt="你是审查员。",
    )


async def test_defined_agent_runs_from_blank_history_and_completes(tmp_path: Path) -> None:
    provider = FakeProvider([_assistant("完成审查")])
    runner = AgentRunner(lambda _: provider, _registry(), workspace_root=tmp_path)

    result = await runner.run(
        AgentLaunchRequest(
            "task-1",
            AgentLaunchKind.DEFINED,
            "审查",
            "parent",
            role=_role(tmp_path),
        ),
        AgentCancelToken(),
    )

    assert result.status is AgentTaskStatus.COMPLETED
    assert result.final_text == "完成审查"
    assert provider.requests[0] == (ChatMessage(Role.USER, content="审查"),)
    assert "你是审查员。" in provider.provider_requests[0].prompt.debug_full_prompt


async def test_fork_agent_preserves_parent_message_prefix(tmp_path: Path) -> None:
    provider = FakeProvider([_assistant("fork 完成")])
    runner = AgentRunner(lambda _: provider, _registry(), workspace_root=tmp_path)
    parent = (
        ChatMessage(Role.USER, content="父任务"),
        ChatMessage(Role.ASSISTANT, content="父回答"),
    )

    await runner.run(
        AgentLaunchRequest(
            "task-1",
            AgentLaunchKind.FORK,
            "继续排查",
            "parent",
            parent_messages=parent,
        ),
        AgentCancelToken(),
    )

    assert provider.requests[0][:2] == parent
    assert provider.requests[0][-1] == ChatMessage(Role.USER, content="继续排查")


async def test_agent_runner_reports_iteration_limit_as_incomplete(tmp_path: Path) -> None:
    tool_call = ToolCall("call-1", "read_file", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_calls=(tool_call,)), TokenUsage())],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_calls=(tool_call,)), TokenUsage())],
        ]
    )
    runner = AgentRunner(lambda _: provider, _registry(), workspace_root=tmp_path)

    result = await runner.run(
        AgentLaunchRequest(
            "task-1",
            AgentLaunchKind.DEFINED,
            "读",
            "parent",
            max_turns=1,
        ),
        AgentCancelToken(),
    )

    assert result.status is AgentTaskStatus.INCOMPLETE
    assert "上限" in (result.error or "")


async def test_agent_runner_respects_empty_visible_tool_set(tmp_path: Path) -> None:
    tool_call = ToolCall("call-1", "read_file", "{}")
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=tool_call), TokenUsage())],
            [_assistant("无法调用工具")],
        ]
    )
    runner = AgentRunner(lambda _: provider, _registry(), workspace_root=tmp_path)

    result = await runner.run(
        AgentLaunchRequest("task-1", AgentLaunchKind.DEFINED, "读文件", "parent"),
        AgentCancelToken(),
    )

    assert result.status is AgentTaskStatus.COMPLETED
    assert provider.tools[0] == ()
    assert provider.requests[1][-1].tool_result is not None
    assert provider.requests[1][-1].tool_result.error_code is ToolErrorCode.UNKNOWN_TOOL


async def test_child_permission_session_rules_do_not_mutate_parent(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    parent = PermissionManager(
        workspace,
        (),
        PermissionPaths.for_workspace(tmp_path),
        {"read_file"},
        mode=PermissionMode.DEFAULT,
        confirmer=lambda _: PermissionConfirmation.SESSION,
    )
    provider = FakeProvider([_assistant("完成")])
    runner = AgentRunner(
        lambda _: provider, _registry(), workspace_root=tmp_path, parent_permissions=parent
    )

    await runner.run(
        AgentLaunchRequest(
            "task-1",
            AgentLaunchKind.DEFINED,
            "读",
            "parent",
            permission_mode=PermissionMode.DEFAULT,
        ),
        AgentCancelToken(),
    )

    assert getattr(parent, "_session_rules") == []
