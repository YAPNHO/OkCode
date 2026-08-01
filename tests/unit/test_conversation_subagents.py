from __future__ import annotations

import pytest

from okcode.agents.models import (
    AgentLaunchKind,
    AgentTaskNotification,
    AgentTaskResult,
    AgentTaskStatus,
    AgentUsage,
)
from okcode.conversation import ConversationSession
from okcode.models import ChatMessage, Role, StreamCompleted, TokenUsage
from okcode.tools.executor import ToolExecutor
from okcode.tools.registry import ToolRegistry
from tests.fakes import FakeProvider


def test_conversation_initial_messages_are_copied_for_fork_snapshot() -> None:
    initial = [ChatMessage(Role.USER, content="旧消息")]
    registry = ToolRegistry()
    session = ConversationSession(
        FakeProvider([]), registry, ToolExecutor(registry), initial_messages=initial
    )
    initial.append(ChatMessage(Role.USER, content="新消息"))

    assert session.messages == (ChatMessage(Role.USER, content="旧消息"),)
    snapshot = session.parent_agent_context(())
    assert snapshot.messages == session.messages
    assert snapshot.visible_tool_names == ()


class NotificationManager:
    def __init__(self, notifications: tuple[AgentTaskNotification, ...]) -> None:
        self.notifications = notifications
        self.drained_session_ids: list[str] = []

    def drain_notifications(self, parent_session_id: str) -> tuple[AgentTaskNotification, ...]:
        self.drained_session_ids.append(parent_session_id)
        notifications = self.notifications
        self.notifications = ()
        return notifications


@pytest.mark.asyncio
async def test_background_agent_notification_is_system_instruction_not_history() -> None:
    registry = ToolRegistry()
    usage = AgentUsage(
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
        model_request_count=2,
        tool_call_count=1,
    )
    manager = NotificationManager(
        (
            AgentTaskNotification(
                "",
                AgentTaskResult(
                    "task-1",
                    AgentLaunchKind.FORK,
                    AgentTaskStatus.COMPLETED,
                    role_name=None,
                    summary="子任务摘要",
                    final_text="子任务最终结果",
                    usage=usage,
                ),
            ),
        )
    )
    provider = FakeProvider(
        [
            StreamCompleted(
                ChatMessage(Role.ASSISTANT, content="父任务继续"),
                TokenUsage(3, 4, 7, True),
            )
        ]
    )
    session = ConversationSession(
        provider,
        registry,
        ToolExecutor(registry),
        agent_task_manager=manager,  # type: ignore[arg-type]
    )

    _ = [event async for event in session.stream_user_message("继续")]

    agent_notes = [
        instruction
        for instruction in provider.provider_requests[0].prompt.dynamic_system
        if instruction.kind == "agent_task"
    ]
    assert len(agent_notes) == 1
    assert "task-1" in agent_notes[0].content
    assert "子任务最终结果" in agent_notes[0].content
    assert session.messages == (
        ChatMessage(Role.USER, content="继续"),
        ChatMessage(Role.ASSISTANT, content="父任务继续"),
    )
    status = session.status_snapshot()
    assert status.child_input_tokens == 11
    assert status.child_output_tokens == 7
    assert status.child_tool_calls == 1
    assert manager.drained_session_ids == [""]
