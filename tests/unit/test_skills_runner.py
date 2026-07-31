from __future__ import annotations

import asyncio
from pathlib import Path

from okcode.models import ChatMessage, Role, StreamCompleted, ToolCall
from okcode.skills.models import (
    SkillActivation,
    SkillExecutionMode,
    SkillHistoryMode,
    SkillSourceKind,
)
from okcode.skills.runner import SkillRunner
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import (
    JSONValue,
    ToolDefinition,
    ToolErrorCode,
    ToolExecutionResult,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.registry import ToolRegistry
from tests.fakes import FakeProvider


class EchoTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "echo",
            "回显",
            {"type": "object", "additionalProperties": False},
            5,
            ToolSafety.READ_ONLY,
        )

    async def execute(self, _: dict[str, JSONValue]) -> ToolOutput:
        return ToolOutput("工具结果")


def _activation() -> SkillActivation:
    return SkillActivation(
        "isolated",
        "说明",
        SkillSourceKind.PROJECT,
        Path("isolated.md"),
        "v1",
        "独立 SOP",
        {},
        ("echo",),
        (),
        SkillExecutionMode.ISOLATED,
        SkillHistoryMode.RECENT,
        "skill-model",
    )


def test_recent_history_keeps_tool_call_and_result_paired() -> None:
    call = ToolCall("call", "echo", "{}")
    result = ToolExecutionResult("call", "echo", False, "失败", ToolErrorCode.UNKNOWN_TOOL)
    messages = (
        ChatMessage(Role.USER, "u0"),
        ChatMessage(Role.ASSISTANT, "a0"),
        ChatMessage(Role.USER, "u1"),
        ChatMessage(Role.ASSISTANT, "", tool_calls=(call,)),
        ChatMessage(Role.TOOL, tool_results=(result,)),
        ChatMessage(Role.USER, "u2"),
        ChatMessage(Role.ASSISTANT, "a2"),
        ChatMessage(Role.USER, "u3"),
        ChatMessage(Role.ASSISTANT, "a3"),
        ChatMessage(Role.USER, "u4"),
        ChatMessage(Role.ASSISTANT, "a4"),
    )
    runner = SkillRunner(FakeProvider([]))

    selected = runner.select_history(messages, SkillHistoryMode.RECENT)

    assert selected[0].role is Role.ASSISTANT
    assert selected[0].tool_calls == (call,)
    assert selected[1].role is Role.TOOL


def test_runner_executes_tool_loop_and_applies_model_override() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    provider = FakeProvider(
        [
            [
                StreamCompleted(
                    ChatMessage(Role.ASSISTANT, "", tool_calls=(ToolCall("call", "echo", "{}"),))
                )
            ],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "完成摘要"))],
        ]
    )
    runner = SkillRunner(provider, executor=ToolExecutor(registry))

    result = asyncio.run(
        runner.run(
            _activation(),
            messages=(),
            tools=registry.definitions(),
            history_mode=SkillHistoryMode.NONE,
        )
    )

    assert result.success is True
    assert result.summary == "完成摘要"
    assert provider.provider_requests[0].model_override == "skill-model"
    assert provider.requests[1][-1].role is Role.TOOL
