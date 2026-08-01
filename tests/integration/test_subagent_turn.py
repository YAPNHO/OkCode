from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from okcode.agents.launcher import AgentLauncher
from okcode.agents.manager import AgentTaskManager
from okcode.agents.models import (
    AgentRole,
    AgentRoleCatalog,
    AgentRoleSourceKind,
    AgentTaskStatus,
)
from okcode.agents.runner import AgentRunner
from okcode.agents.tool import AgentTool
from okcode.conversation import ConversationSession
from okcode.models import ChatMessage, Role, StreamCompleted, StreamEvent, TokenUsage, ToolCall
from okcode.tools.executor import ToolExecutor
from okcode.tools.registry import ToolRegistry
from tests.fakes import FakeProvider


def _role(tmp_path: Path) -> AgentRole:
    return AgentRole(
        name="reviewer",
        description="审查代码",
        source_kind=AgentRoleSourceKind.PROJECT,
        source_path=tmp_path / "reviewer.md",
        system_prompt="你是独立代码审查子 Agent。",
    )


def _assistant(text: str) -> StreamCompleted:
    return StreamCompleted(ChatMessage(Role.ASSISTANT, content=text), TokenUsage(5, 3, 8, True))


def _session_with_agent(
    tmp_path: Path,
    main_provider: FakeProvider,
    child_provider: FakeProvider,
) -> tuple[ConversationSession, AgentTaskManager]:
    registry = ToolRegistry()
    runner = AgentRunner(lambda _: child_provider, registry, workspace_root=tmp_path)
    manager = AgentTaskManager(runner)
    launcher = AgentLauncher(AgentRoleCatalog({"reviewer": _role(tmp_path)}), registry, manager)
    holder: dict[str, ConversationSession] = {}
    registry.register(
        AgentTool(
            launcher,
            lambda: holder["session"].parent_agent_context(registry.definitions()),
        )
    )
    session = ConversationSession(
        main_provider,
        registry,
        ToolExecutor(registry),
        agent_task_manager=manager,
        workspace_root=tmp_path,
    )
    holder["session"] = session
    return session, manager


class GatedProvider(FakeProvider):
    def __init__(
        self,
        gate: threading.Event,
        events: list[StreamEvent | Exception] | list[list[StreamEvent | Exception]],
    ) -> None:
        super().__init__(events)
        self._gate = gate

    async def _stream(self, script: list[StreamEvent | Exception]) -> AsyncIterator[StreamEvent]:
        opened = await asyncio.to_thread(self._gate.wait, 2)
        if not opened:
            raise TimeoutError("等待测试闸门超时")
        try:
            for event in script:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            self.stream_closed = True
            self.stream_closed_count += 1


@pytest.mark.asyncio
async def test_defined_subagent_tool_turn_returns_child_result(tmp_path: Path) -> None:
    call = ToolCall(
        "agent-call",
        "agent",
        '{"kind":"defined","role":"reviewer","task":"审查这段代码"}',
    )
    main_provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call), TokenUsage())],
            [_assistant("主 Agent 已收到审查结果")],
        ]
    )
    child_provider = FakeProvider([_assistant("子 Agent 审查完成")])
    session, manager = _session_with_agent(tmp_path, main_provider, child_provider)

    try:
        _ = [event async for event in session.stream_user_message("委派审查")]
    finally:
        manager.close()

    tool_result = session.messages[-2].tool_result
    assert tool_result is not None
    data = json.loads(tool_result.content)
    assert data["status"] == "completed"
    assert data["final_text"] == "子 Agent 审查完成"
    assert child_provider.requests[0] == (ChatMessage(Role.USER, content="审查这段代码"),)
    role_notes = [
        item
        for item in child_provider.provider_requests[0].prompt.dynamic_system
        if item.kind == "subagent_role"
    ]
    assert len(role_notes) == 1
    assert "独立代码审查" in role_notes[0].content
    assert session.messages[-1] == ChatMessage(Role.ASSISTANT, content="主 Agent 已收到审查结果")


@pytest.mark.asyncio
async def test_fork_subagent_runs_in_background_and_notifies_next_turn(tmp_path: Path) -> None:
    gate = threading.Event()
    call = ToolCall("agent-call", "agent", '{"kind":"fork","task":"基于背景继续排查"}')
    main_provider = FakeProvider(
        [
            [_assistant("背景已记录")],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, tool_call=call), TokenUsage())],
            [_assistant("已切到后台")],
            [_assistant("已读取后台结果")],
        ]
    )
    child_provider = GatedProvider(gate, [_assistant("Fork 子 Agent 结果")])
    session, manager = _session_with_agent(tmp_path, main_provider, child_provider)

    try:
        _ = [event async for event in session.stream_user_message("先记录背景")]
        _ = [event async for event in session.stream_user_message("启动 fork 子任务")]

        tool_result = session.messages[-2].tool_result
        assert tool_result is not None
        task_id = json.loads(tool_result.content)["task_id"]
        assert json.loads(tool_result.content)["status"] == "background"

        gate.set()
        await _wait_for_task(manager, task_id, AgentTaskStatus.COMPLETED)

        _ = [event async for event in session.stream_user_message("汇总后台结果")]
    finally:
        gate.set()
        manager.close()

    child_messages = child_provider.requests[0]
    assert child_messages[:2] == (
        ChatMessage(Role.USER, content="先记录背景"),
        ChatMessage(Role.ASSISTANT, content="背景已记录"),
    )
    assert child_messages[-1] == ChatMessage(Role.USER, content="基于背景继续排查")
    agent_notes = [
        item
        for item in main_provider.provider_requests[-1].prompt.dynamic_system
        if item.kind == "agent_task"
    ]
    assert len(agent_notes) == 1
    assert task_id in agent_notes[0].content
    assert "Fork 子 Agent 结果" in agent_notes[0].content
    assert session.messages[-1] == ChatMessage(Role.ASSISTANT, content="已读取后台结果")


async def _wait_for_task(
    manager: AgentTaskManager,
    task_id: str,
    status: AgentTaskStatus,
    *,
    timeout_seconds: float = 2.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        snapshot = manager.get_snapshot(task_id)
        if snapshot.status is status:
            return
        await asyncio.sleep(0.01)
    snapshot = manager.get_snapshot(task_id)
    raise AssertionError(f"任务 {task_id} 未达到 {status.value}，当前状态：{snapshot.status.value}")
