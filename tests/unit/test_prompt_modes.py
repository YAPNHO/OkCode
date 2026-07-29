from __future__ import annotations

from okcode.prompt import TaskModeInstructionPlanner, TurnKind


def test_plan_mode_uses_full_key_and_compact_instructions() -> None:
    planner = TaskModeInstructionPlanner()

    first = planner.build(TurnKind.PLAN, 1)
    second = planner.build(TurnKind.PLAN, 2)
    fourth = planner.build(TurnKind.PLAN, 4)

    assert first is not None and "先通过只读工具" in first
    assert second is not None and "规划模式提醒" in second
    assert fourth is not None and "仍处于规划模式" in fourth


def test_do_and_normal_mode_have_separate_rules() -> None:
    planner = TaskModeInstructionPlanner()

    do_instruction = planner.build(TurnKind.DO, 1)

    assert do_instruction is not None and "执行计划模式" in do_instruction
    assert planner.build(TurnKind.NORMAL, 1) is None
