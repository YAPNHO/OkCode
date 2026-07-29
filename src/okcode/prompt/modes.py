"""任务模式与按轮次注入策略。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TurnKind(StrEnum):
    """一次用户输入对应的会话任务类型。"""

    NORMAL = "normal"
    PLAN = "plan"
    DO = "do"


@dataclass(frozen=True, slots=True)
class TaskModeSchedule:
    """控制完整、关键和精简任务模式指令的频率。"""

    repeat_every: int = 4

    def __post_init__(self) -> None:
        if self.repeat_every <= 1:
            raise ValueError("任务模式重复间隔必须大于 1。")


class TaskModeInstructionPlanner:
    """按模型迭代次数生成任务模式补充指令。"""

    def __init__(self, schedule: TaskModeSchedule | None = None) -> None:
        self._schedule = schedule or TaskModeSchedule()

    def build(self, turn_kind: TurnKind, iteration: int) -> str | None:
        """返回本次迭代需要注入的模式规则；普通任务不注入。"""

        if iteration <= 0:
            raise ValueError("模型迭代次数必须为正数。")
        if turn_kind is TurnKind.NORMAL:
            return None
        if turn_kind is TurnKind.DO:
            return (
                "当前处于执行计划模式：使用已保存计划推进任务；修改前先读取目标内容，"
                "完成后运行验证并如实报告结果。"
            )
        if iteration == 1:
            return (
                "当前处于规划模式：先通过只读工具调研现状，再给出按顺序可执行的计划。"
                "不要修改文件、不要执行有副作用命令；计划必须说明文件、步骤和验证方式。"
            )
        if iteration % self._schedule.repeat_every == 0:
            return "仍处于规划模式：仅使用只读工具，先依据真实调研结果完善计划，不要修改文件。"
        return "规划模式提醒：继续只读调研，不要修改文件。"
