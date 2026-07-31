from __future__ import annotations

from okcode.hooks import HookContext, HookEvent
from okcode.hooks.models import (
    ConditionMode,
    HookCondition,
    HookConditionGroup,
    HookControl,
    HookRule,
    PromptHookAction,
)
from okcode.matching import parse_match_expression


def test_hook_context_reads_flat_values() -> None:
    context = HookContext(HookEvent.TOOL_BEFORE, {"tool.name": "write_file"})

    assert context.value("tool.name") == "write_file"
    assert context.value("missing") is None


def test_rule_renders_condition_summary() -> None:
    rule = HookRule(
        "r1",
        HookEvent.MESSAGE_USER,
        HookConditionGroup(
            ConditionMode.ALL,
            (HookCondition("message.content", parse_match_expression("glob:*hi*")),),
        ),
        PromptHookAction("请保持简洁。"),
        HookControl(),
    )

    assert rule.condition_summary() == "message.content=glob:*hi*"


def test_unconditional_rule_summary() -> None:
    rule = HookRule(
        "r1",
        HookEvent.SESSION_START,
        None,
        PromptHookAction("启动提示"),
        HookControl(),
    )

    assert rule.condition_summary() == "无条件"
