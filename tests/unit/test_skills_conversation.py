from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

from okcode.commands import CommandContext, CommandDispatcher, build_default_command_registry
from okcode.conversation import ConversationSession
from okcode.models import ChatMessage, Role, StreamCompleted, ToolCall
from okcode.prompt import PromptBuildContext, PromptOptionalSections, TurnKind
from okcode.skills.activation import SkillActivationStore
from okcode.skills.catalog import SkillCatalog
from okcode.skills.discovery import SkillRoots
from okcode.skills.runtime import SkillRuntime
from okcode.skills.tools import LoadSkillTool
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import JSONValue, ToolDefinition, ToolOutput, ToolSafety
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

    async def execute(self, _: Mapping[str, JSONValue]) -> ToolOutput:
        return ToolOutput("ok")


def _runtime(tmp_path: Path) -> SkillRuntime:
    project = tmp_path / ".okcode" / "skills"
    project.mkdir(parents=True)
    (project / "commit.md").write_text(
        "---\n"
        "name: commit\n"
        "description: 提交改动\n"
        "tools: [echo]\n"
        "mode: shared\n"
        "history: recent\n"
        "model: skill-model\n"
        "---\n\n"
        "完整 commit SOP。",
        encoding="utf-8",
    )
    roots = SkillRoots(tmp_path / "builtin", tmp_path / "user", project)
    command_registry = build_default_command_registry()
    runtime = SkillRuntime(
        SkillCatalog.discover(roots, {"echo"}),
        SkillActivationStore(),
        command_registry,
    )
    runtime.refresh()
    return runtime


def test_commit_command_loads_sop_for_next_request_and_narrows_tools(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    registry = ToolRegistry()
    registry.register(EchoTool())
    load_tool = LoadSkillTool(
        runtime.catalog,
        runtime.activation_store,
        registry,
        refresh_callback=runtime.refresh,
    )
    registry.register(load_tool)
    provider = FakeProvider(
        [
            [
                StreamCompleted(
                    ChatMessage(
                        Role.ASSISTANT,
                        "",
                        tool_calls=(ToolCall("load", "load_skill", '{"name":"commit"}'),),
                    )
                )
            ],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, "审查完成"))],
        ]
    )

    def context_factory(
        turn_kind: TurnKind,
        iteration: int,
        tools: Sequence[ToolDefinition],
    ) -> PromptBuildContext:
        return PromptBuildContext(
            workspace_root=str(tmp_path),
            platform="Windows",
            current_date="2026-07-31",
            available_tool_names=tuple(tool.name for tool in tools),
            turn_kind=turn_kind,
            iteration=iteration,
            optional_sections=PromptOptionalSections(
                available_skills=runtime.render_available_section(),
                active_skills=runtime.render_active_section(),
            ),
        )

    session = ConversationSession(
        provider,
        registry,
        ToolExecutor(registry),
        context_factory=context_factory,
        skill_runtime=runtime,
    )

    assert runtime.command_registry is not None
    dispatched = asyncio.run(
        CommandDispatcher(runtime.command_registry).dispatch(
            "/commit 提交当前改动",
            CommandContext(
                object(),
                runtime.command_registry,
                session,
                tmp_path,
                runtime,
            ),
        )
    )
    assert dispatched.command_result is not None
    assert dispatched.command_result.forward is not None
    forward = dispatched.command_result.forward
    assert "'commit'" in forward.content
    assert "load_skill" in forward.content
    assert "完整 commit SOP" not in forward.content

    asyncio.run(
        _consume(
            session.stream_user_message(
                forward.content,
                mode=forward.runtime_mode,
                tool_scope=forward.tool_scope,
            )
        )
    )

    first, second = provider.provider_requests
    first_prompt = "\n".join(item.content for item in first.prompt.dynamic_system)
    second_prompt = "\n".join(item.content for item in second.prompt.dynamic_system)
    assert "commit：提交改动" in first_prompt
    assert "完整 commit SOP" not in first_prompt
    assert "完整 commit SOP" in second_prompt
    assert [tool.name for tool in second.tools] == ["echo", "load_skill"]
    assert second.model_override == "skill-model"

    session.reset_session()
    assert runtime.render_active_section() == ""


async def _consume(stream: object) -> None:
    async for _ in stream:  # type: ignore[union-attr]
        pass
