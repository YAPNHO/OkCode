"""Hook 事件分发、状态和失败隔离。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from okcode.hooks.actions import HookActionOutcome, HookActionRunner
from okcode.hooks.models import (
    HookContext,
    HookEvent,
    HookInterception,
    HookRule,
    HookRunRecord,
    PromptScope,
)
from okcode.prompt.builder import SystemInstruction

_LOG = logging.getLogger(__name__)


class HookRuntime:
    """持有已加载 Hook 规则和本进程运行状态。"""

    def __init__(
        self,
        rules: Sequence[HookRule],
        *,
        runner: HookActionRunner | None = None,
        config_path: str = "",
    ) -> None:
        self._rules = tuple(rules)
        self._runner = runner
        self._config_path = config_path
        self._records: list[HookRunRecord] = []
        self._once_done: set[str] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._next_request: list[SystemInstruction] = []
        self._turn: list[SystemInstruction] = []
        self._session: list[SystemInstruction] = []

    @property
    def records(self) -> tuple[HookRunRecord, ...]:
        return tuple(self._records)

    @property
    def config_path(self) -> str:
        return self._config_path

    async def dispatch(self, context: HookContext) -> HookInterception | None:
        """分发一次事件，只有前置拦截可返回 HookInterception。"""

        for rule in self._rules:
            if rule.event is not context.event:
                continue
            if not rule.enabled:
                self._record(rule, context.event, "skipped", "规则已禁用。")
                continue
            if rule.control.once and rule.identifier in self._once_done:
                self._record(rule, context.event, "skipped", "once 规则已执行。")
                continue
            if not self._matches(rule, context):
                self._record(rule, context.event, "skipped", "条件未命中。")
                continue
            if rule.control.once:
                self._once_done.add(rule.identifier)
            if rule.control.background:
                self._schedule_background(rule, context)
                continue
            outcome = await self._run_rule(rule, context)
            if outcome.interception is not None:
                return outcome.interception
        return None

    def system_instructions(self) -> tuple[SystemInstruction, ...]:
        """返回当前模型请求可见的 Hook 注入指令。"""

        return tuple((*self._session, *self._turn, *self._next_request))

    def mark_request_dispatched(self) -> None:
        """真实 Provider 请求开始后消费 next_request 注入。"""

        self._next_request.clear()

    def end_turn(self) -> None:
        """轮次结束时清理 turn 作用域注入。"""

        self._turn.clear()

    def list_entries(self) -> tuple[object, ...]:
        """返回 /hooks 展示条目。"""

        from okcode.models import HookListEntry

        return tuple(
            HookListEntry(
                rule.identifier,
                rule.event.value,
                rule.condition_summary(),
                rule.action.type.value,
                rule.enabled,
                rule.control.once,
                rule.control.background,
                rule.control.timeout_seconds,
                rule.action.type.value == "subagent",
            )
            for rule in self._rules
        )

    async def aclose(self) -> None:
        """取消后台任务，不让异常泄漏到应用退出。"""

        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    def _matches(self, rule: HookRule, context: HookContext) -> bool:
        group = rule.conditions
        if group is None:
            return True
        values = []
        for condition in group.conditions:
            value = context.value(condition.field)
            if value is None:
                values.append(False)
                continue
            values.append(condition.expression.matches(_stable_text(value)))
        if group.mode.value == "all":
            return all(values)
        return any(values)

    async def _run_rule(self, rule: HookRule, context: HookContext) -> HookActionOutcome:
        runner = self._runner
        if runner is None:
            self._record(rule, context.event, "skipped", "HookRunner 未配置。")
            return HookActionOutcome("skipped", "HookRunner 未配置。")
        try:
            outcome = await runner.run(rule, context)
        except Exception as exc:
            _LOG.info("Hook 运行失败：%s", exc)
            self._record(rule, context.event, "failed", "Hook 运行失败。")
            return HookActionOutcome("failed", "Hook 运行失败。")
        self._apply_outcome(rule, context.event, outcome)
        return outcome

    def _schedule_background(self, rule: HookRule, context: HookContext) -> None:
        async def run() -> None:
            await self._run_rule(rule, context)

        task = asyncio.create_task(run())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_done)
        self._record(rule, context.event, "scheduled", "后台 Hook 已调度。")

    def _background_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _LOG.info("后台 Hook 失败：%s", exc)

    def _apply_outcome(self, rule: HookRule, event: HookEvent, outcome: HookActionOutcome) -> None:
        if outcome.prompt_content is not None and outcome.prompt_scope is not None:
            instruction = SystemInstruction(
                "hook",
                f"[{rule.identifier}] {outcome.prompt_content}",
                priority=110,
            )
            if outcome.prompt_scope is PromptScope.NEXT_REQUEST:
                self._next_request.append(instruction)
            elif outcome.prompt_scope is PromptScope.TURN:
                self._turn.append(instruction)
            else:
                self._session.append(instruction)
        self._record(rule, event, outcome.status, outcome.message)

    def _record(self, rule: HookRule, event: HookEvent, status: str, message: str = "") -> None:
        self._records.append(HookRunRecord(rule.identifier, event, status, message))


def _stable_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
