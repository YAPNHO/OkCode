"""子 Agent 后台通知格式化。"""

from __future__ import annotations

from okcode.agents.models import AgentTaskNotification, AgentTaskResult
from okcode.prompt.builder import SystemInstruction

_SUMMARY_LIMIT = 1200
_TEXT_LIMIT = 2000
_ERROR_LIMIT = 800


class AgentNotificationBridge:
    """把任务结果转换为主对话可见的系统补充说明。"""

    def to_system_instruction(self, notification: AgentTaskNotification) -> SystemInstruction:
        result = notification.result
        return SystemInstruction(
            kind="agent_task",
            priority=110,
            content=format_task_notification(result),
        )


def format_task_notification(result: AgentTaskResult) -> str:
    """生成有边界的子 Agent 任务通知。"""

    lines = [
        "子 Agent 后台任务已更新。",
        f"- 任务 ID：{result.task_id}",
        f"- 类型：{result.kind.value}",
        f"- 状态：{result.status.value}",
    ]
    if result.role_name:
        lines.append(f"- 角色：{result.role_name}")
    if result.worktree is not None:
        lines.extend(
            [
                f"- 隔离工作区：{result.worktree.path}",
                f"- Worktree 分支：{result.worktree.branch}",
                f"- Worktree 清理：{result.worktree.cleanup_decision.value}",
                f"- Worktree 说明：{_limit(result.worktree.cleanup_message, _ERROR_LIMIT)}",
            ]
        )
        if result.worktree.protection_reasons:
            reasons = ", ".join(reason.value for reason in result.worktree.protection_reasons)
            lines.append(f"- Worktree 保留原因：{reasons}")
    if result.summary:
        lines.append(f"- 摘要：{_limit(result.summary, _SUMMARY_LIMIT)}")
    if result.error:
        lines.append(f"- 错误：{_limit(result.error, _ERROR_LIMIT)}")
    if result.final_text:
        lines.append(f"- 最终回答：{_limit(result.final_text, _TEXT_LIMIT)}")
    if result.full_result_ref:
        lines.append(f"- 完整结果引用：{result.full_result_ref}")
    lines.append(
        "- 用量："
        f"输入 {result.usage.input_tokens}，"
        f"输出 {result.usage.output_tokens}，"
        f"请求 {result.usage.model_request_count}，"
        f"工具 {result.usage.tool_call_count}"
    )
    return "\n".join(lines)


def _limit(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[内容已截断，完整结果请查看任务引用。]"
