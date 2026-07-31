from __future__ import annotations

import asyncio

import pytest

from okcode.hooks.actions import HookActionOutcome
from okcode.hooks.models import (
    ConditionMode,
    HookCondition,
    HookConditionGroup,
    HookContext,
    HookControl,
    HookEvent,
    HookInterception,
    HookRule,
    PromptHookAction,
    PromptScope,
    ShellHookAction,
)
from okcode.hooks.runtime import HookRuntime
from okcode.matching import parse_match_expression


class RecordingRunner:
    def __init__(self) -> None:
        self.rules: list[str] = []
        self.outcomes: dict[str, HookActionOutcome] = {}

    async def run(self, rule: HookRule, context: HookContext) -> HookActionOutcome:
        self.rules.append(rule.identifier)
        return self.outcomes.get(rule.identifier, HookActionOutcome("ok", "done"))


def _rule(
    identifier: str,
    *,
    condition: HookConditionGroup | None = None,
    control: HookControl | None = None,
    event: HookEvent = HookEvent.MESSAGE_USER,
    enabled: bool = True,
) -> HookRule:
    return HookRule(
        identifier,
        event,
        condition,
        ShellHookAction("echo ok"),
        control or HookControl(),
        enabled,
    )


@pytest.mark.asyncio
async def test_dispatch_matches_conditions_and_order() -> None:
    runner = RecordingRunner()
    condition = HookConditionGroup(
        ConditionMode.ALL,
        (HookCondition("message.content", parse_match_expression("glob:*hi*")),),
    )
    runtime = HookRuntime(
        (
            _rule("disabled", enabled=False),
            _rule("miss", condition=condition),
            _rule("hit"),
        ),
        runner=runner,  # type: ignore[arg-type]
    )

    await runtime.dispatch(HookContext(HookEvent.MESSAGE_USER, {"message.content": "bye"}))

    assert runner.rules == ["hit"]
    assert [record.status for record in runtime.records][:2] == ["skipped", "skipped"]


@pytest.mark.asyncio
async def test_once_rule_runs_only_once() -> None:
    runner = RecordingRunner()
    runtime = HookRuntime(
        (_rule("once", control=HookControl(once=True)),),
        runner=runner,  # type: ignore[arg-type]
    )
    context = HookContext(HookEvent.MESSAGE_USER, {"message.content": "hi"})

    await runtime.dispatch(context)
    await runtime.dispatch(context)

    assert runner.rules == ["once"]
    assert runtime.records[-1].message == "once 规则已执行。"


@pytest.mark.asyncio
async def test_dispatch_returns_interception() -> None:
    runner = RecordingRunner()
    runner.outcomes["guard"] = HookActionOutcome(
        "intercepted",
        interception=HookInterception("拒绝", "guard"),
    )
    runtime = HookRuntime((_rule("guard", event=HookEvent.TOOL_BEFORE),), runner=runner)  # type: ignore[arg-type]

    result = await runtime.dispatch(HookContext(HookEvent.TOOL_BEFORE, {"tool.name": "x"}))

    assert result == HookInterception("拒绝", "guard")


@pytest.mark.asyncio
async def test_prompt_scopes_are_consumed_correctly() -> None:
    runner = RecordingRunner()
    runner.outcomes["p"] = HookActionOutcome(
        "prompt",
        prompt_content="下一次请求",
        prompt_scope=PromptScope.NEXT_REQUEST,
    )
    runner.outcomes["turn"] = HookActionOutcome(
        "prompt",
        prompt_content="本轮",
        prompt_scope=PromptScope.TURN,
    )
    runtime = HookRuntime(
        (
            HookRule(
                "p",
                HookEvent.MESSAGE_USER,
                None,
                PromptHookAction("下一次请求"),
                HookControl(),
            ),
            HookRule(
                "turn",
                HookEvent.MESSAGE_USER,
                None,
                PromptHookAction("本轮", scope=PromptScope.TURN),
                HookControl(),
            ),
        ),
        runner=runner,  # type: ignore[arg-type]
    )

    await runtime.dispatch(HookContext(HookEvent.MESSAGE_USER, {"message.content": "hi"}))
    assert [item.content for item in runtime.system_instructions()] == [
        "[turn] 本轮",
        "[p] 下一次请求",
    ]
    runtime.mark_request_dispatched()
    assert [item.content for item in runtime.system_instructions()] == ["[turn] 本轮"]
    runtime.end_turn()
    assert runtime.system_instructions() == ()


@pytest.mark.asyncio
async def test_background_rule_does_not_block_and_is_closed() -> None:
    class SlowRunner:
        async def run(self, rule: HookRule, context: HookContext) -> HookActionOutcome:
            await asyncio.sleep(10)
            return HookActionOutcome("ok")

    runtime = HookRuntime(
        (_rule("bg", control=HookControl(background=True)),),
        runner=SlowRunner(),  # type: ignore[arg-type]
    )
    await runtime.dispatch(HookContext(HookEvent.MESSAGE_USER, {"message.content": "hi"}))

    assert runtime.records[-1].status == "scheduled"
    await runtime.aclose()
