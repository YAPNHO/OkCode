from __future__ import annotations

from okcode.agents.models import (
    AgentLaunchKind,
    AgentTaskNotification,
    AgentTaskResult,
    AgentTaskStatus,
    AgentUsage,
)
from okcode.agents.notifications import AgentNotificationBridge, format_task_notification


def test_notification_formats_result_as_system_instruction() -> None:
    result = AgentTaskResult(
        task_id="task-1",
        kind=AgentLaunchKind.DEFINED,
        status=AgentTaskStatus.COMPLETED,
        role_name="reviewer",
        summary="审查完成",
        final_text="没有发现问题",
        usage=AgentUsage(input_tokens=10, output_tokens=5, model_request_count=1),
    )

    instruction = AgentNotificationBridge().to_system_instruction(
        AgentTaskNotification("parent", result)
    )

    assert instruction.kind == "agent_task"
    assert "任务 ID：task-1" in instruction.content
    assert "角色：reviewer" in instruction.content
    assert "输入 10" in instruction.content


def test_notification_limits_long_text_and_keeps_full_result_ref() -> None:
    result = AgentTaskResult(
        task_id="task-1",
        kind=AgentLaunchKind.FORK,
        status=AgentTaskStatus.COMPLETED,
        summary="s" * 1300,
        final_text="x" * 2100,
        full_result_ref=".okcode/agents/task-1.txt",
    )

    text = format_task_notification(result)

    assert "内容已截断" in text
    assert ".okcode/agents/task-1.txt" in text
    assert len(text) < 4000
