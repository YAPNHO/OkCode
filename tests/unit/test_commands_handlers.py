from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from okcode.commands import (
    CommandContext,
    CommandDispatcher,
    CommandKind,
    RuntimeMode,
    ToolScope,
    build_default_command_registry,
)
from okcode.commands.models import (
    CommandMemorySnapshot,
    CommandSessionSnapshot,
    CommandStatusSnapshot,
)
from okcode.models import (
    AgentTaskListEvent,
    CommandHelp,
    CommandMemory,
    CommandNotice,
    CommandSession,
    CommandStatus,
    HookListEvent,
    PermissionStatus,
    RuntimeModeChanged,
    TurnEvent,
)

REVIEW_PROMPT = (
    "Please review the current git diff for code changes. Focus on:\n"
    "1. Logic errors\n"
    "2. Security issues\n"
    "3. Performance problems\n"
    "4. Code style"
)


class DummyConversation:
    def __init__(self) -> None:
        self.mode = RuntimeMode.DEFAULT
        self.permission_mode = "default"
        self.user_messages: list[tuple[str, RuntimeMode | None, ToolScope | None]] = []

    @property
    def runtime_mode(self) -> RuntimeMode:
        return self.mode

    def set_runtime_mode(self, mode: RuntimeMode) -> None:
        self.mode = mode

    def status_snapshot(self) -> CommandStatusSnapshot:
        return CommandStatusSnapshot("plan", 10, 20, 3, 4, "model-x", "D:/repo")

    def memory_snapshot(self) -> CommandMemorySnapshot:
        return CommandMemorySnapshot(("PROJECT.md",), ("USER.md",))

    def permission_string(self) -> str:
        return self.permission_mode

    def permission_status(self, message: str | None = None) -> TurnEvent:
        return PermissionStatus(
            self.permission_mode,
            "default",
            "C:/user/.okcode/permissions.yaml",
            "D:/repo/.okcode/permissions.yaml",
            "D:/repo/.okcode/permissions.local.yaml",
            message,
        )

    def set_permission_mode(self, mode: str) -> TurnEvent:
        if mode not in {"strict", "default", "allow"}:
            return self.permission_status("权限模式只能是 strict、default 或 allow。")
        self.permission_mode = mode
        return self.permission_status("权限模式已更新。")

    def hook_list_event(self) -> TurnEvent:
        return HookListEvent((), "D:/repo/.okcode/hooks.yaml")

    def agent_task_list_event(self) -> TurnEvent:
        return AgentTaskListEvent(())

    def cancel_agent_task(self, task_id: str) -> TurnEvent:
        return CommandNotice(f"cancel {task_id}")

    def background_agent_task(self, task_id: str) -> TurnEvent:
        return CommandNotice(f"background {task_id}")

    def create_team(self, name: str) -> TurnEvent:
        return CommandNotice(f"team create {name}")

    def use_team(self, name: str) -> TurnEvent:
        return CommandNotice(f"team use {name}")

    def leave_team(self) -> TurnEvent:
        return CommandNotice("team leave")

    def team_status_event(self) -> TurnEvent:
        return CommandNotice("team status")

    def session_snapshot(self) -> CommandSessionSnapshot:
        return CommandSessionSnapshot("session-1", "D:/repo/session.jsonl")

    def list_resumable_sessions(self) -> tuple[object, ...]:
        return ()

    async def restore_session(self, session_id: str) -> AsyncIterator[TurnEvent]:
        yield CommandNotice(session_id)

    async def stream_manual_compaction(self) -> AsyncIterator[TurnEvent]:
        yield CommandNotice("compact")

    def reset_session(self) -> TurnEvent:
        return CommandNotice("reset")

    async def stream_do_instruction(self) -> AsyncIterator[TurnEvent]:
        yield CommandNotice("do")

    async def stream_user_message(
        self,
        text: str,
        *,
        mode: RuntimeMode | None = None,
        tool_scope: ToolScope | None = None,
    ) -> AsyncIterator[TurnEvent]:
        self.user_messages.append((text, mode, tool_scope))
        yield CommandNotice(text)


def _context(conversation: DummyConversation, registry=None) -> CommandContext:
    command_registry = registry or build_default_command_registry()
    return CommandContext(object(), command_registry, conversation, Path.cwd())


async def _dispatch(command: str, conversation: DummyConversation):
    registry = build_default_command_registry()
    dispatcher = CommandDispatcher(registry)
    return await dispatcher.dispatch(command, _context(conversation, registry))


def test_default_registry_contains_builtin_commands_including_skill() -> None:
    registry = build_default_command_registry()
    expected = {
        "clear": CommandKind.UI,
        "compact": CommandKind.UI,
        "do": CommandKind.PROMPT,
        "exit": CommandKind.UI,
        "help": CommandKind.LOCAL,
        "hooks": CommandKind.LOCAL,
        "memory": CommandKind.LOCAL,
        "permission": CommandKind.LOCAL,
        "plan": CommandKind.UI,
        "resume": CommandKind.UI,
        "review": CommandKind.PROMPT,
        "session": CommandKind.LOCAL,
        "skill": CommandKind.LOCAL,
        "status": CommandKind.LOCAL,
        "tasks": CommandKind.LOCAL,
        "team": CommandKind.LOCAL,
    }

    visible = registry.visible_commands()

    assert [command.name for command in visible] == sorted(expected)
    assert {command.name: command.kind for command in visible} == expected
    assert registry.resolve("permissions") is None


@pytest.mark.asyncio
async def test_help_lists_visible_commands_sorted_by_name() -> None:
    result = await _dispatch("/help", DummyConversation())

    assert result.command_result is not None
    event = result.command_result.events[0]
    assert isinstance(event, CommandHelp)
    assert [entry.name for entry in event.entries] == sorted(entry.name for entry in event.entries)
    assert len(event.entries) == 16


@pytest.mark.asyncio
async def test_local_status_memory_permission_and_session_commands() -> None:
    conversation = DummyConversation()

    status = await _dispatch("/status", conversation)
    memory = await _dispatch("/memory", conversation)
    permission = await _dispatch("/permission", conversation)
    hooks = await _dispatch("/hooks", conversation)
    session = await _dispatch("/session", conversation)
    tasks = await _dispatch("/tasks", conversation)

    assert isinstance(status.command_result.events[0], CommandStatus)  # type: ignore[union-attr]
    assert status.command_result.events[0].model_name == "model-x"  # type: ignore[union-attr]
    assert isinstance(memory.command_result.events[0], CommandMemory)  # type: ignore[union-attr]
    assert memory.command_result.events[0].project_memory_files == ("PROJECT.md",)  # type: ignore[union-attr]
    assert isinstance(permission.command_result.events[0], PermissionStatus)  # type: ignore[union-attr]
    assert permission.command_result.events[0].current_mode == "default"  # type: ignore[union-attr]
    assert hooks.command_result.events == (HookListEvent((), "D:/repo/.okcode/hooks.yaml"),)  # type: ignore[union-attr]
    assert isinstance(session.command_result.events[0], CommandSession)  # type: ignore[union-attr]
    assert session.command_result.events[0].session_id == "session-1"  # type: ignore[union-attr]
    assert tasks.command_result.events == (AgentTaskListEvent(()),)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_permission_command_updates_mode_without_plural_alias() -> None:
    conversation = DummyConversation()

    allow = await _dispatch("/permission allow", conversation)
    unknown = await _dispatch("/permissions strict", conversation)
    invalid = await _dispatch("/permission unsafe", conversation)
    bad_usage = await _dispatch("/permission allow now", conversation)

    assert conversation.permission_mode == "allow"
    assert allow.command_result.events[0].current_mode == "allow"  # type: ignore[union-attr]
    assert allow.command_result.events[0].message == "权限模式已更新。"  # type: ignore[union-attr]
    assert isinstance(unknown.command_result.events[0], CommandNotice)  # type: ignore[union-attr]
    assert unknown.command_result.events[0].message == (  # type: ignore[union-attr]
        "未知命令：/permissions。输入 /help 查看可用命令。"
    )
    assert invalid.command_result.events[0].current_mode == "allow"  # type: ignore[union-attr]
    assert invalid.command_result.events[0].message == "权限模式只能是 strict、default 或 allow。"  # type: ignore[union-attr]
    assert bad_usage.command_result.events[0].message == "用法：/permission [strict|default|allow]"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_tasks_control_commands_call_conversation_port() -> None:
    conversation = DummyConversation()

    cancel = await _dispatch("/tasks cancel abc", conversation)
    background = await _dispatch("/tasks background abc", conversation)
    bad = await _dispatch("/tasks nope", conversation)

    assert cancel.command_result.events == (CommandNotice("cancel abc"),)  # type: ignore[union-attr]
    assert background.command_result.events == (CommandNotice("background abc"),)  # type: ignore[union-attr]
    assert "用法" in bad.command_result.events[0].message  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_team_commands_call_conversation_port() -> None:
    conversation = DummyConversation()

    status = await _dispatch("/team", conversation)
    create = await _dispatch("/team create core", conversation)
    use = await _dispatch("/team use core", conversation)
    leave = await _dispatch("/team leave", conversation)
    bad = await _dispatch("/team create", conversation)

    assert status.command_result.events == (CommandNotice("team status"),)  # type: ignore[union-attr]
    assert create.command_result.events == (CommandNotice("team create core"),)  # type: ignore[union-attr]
    assert use.command_result.events == (CommandNotice("team use core"),)  # type: ignore[union-attr]
    assert leave.command_result.events == (CommandNotice("team leave"),)  # type: ignore[union-attr]
    assert "team create" in bad.command_result.events[0].message  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_plan_and_do_update_runtime_mode_and_do_streams_saved_plan_instruction() -> None:
    conversation = DummyConversation()

    plan = await _dispatch("/plan", conversation)
    do = await _dispatch("/do", conversation)

    assert conversation.mode is RuntimeMode.DEFAULT
    assert isinstance(plan.command_result.events[0], RuntimeModeChanged)  # type: ignore[union-attr]
    assert plan.command_result.events[0].mode == RuntimeMode.PLAN.value  # type: ignore[union-attr]
    assert do.command_result is not None
    assert do.command_result.stream is not None
    streamed = [event async for event in do.command_result.stream]
    assert streamed == [CommandNotice("do")]


@pytest.mark.asyncio
async def test_prompt_review_command_injects_fixed_text_without_preloading_context() -> None:
    conversation = DummyConversation()

    review = await _dispatch("/review", conversation)

    assert review.command_result is not None
    assert review.command_result.forward is not None
    assert review.command_result.forward.content == REVIEW_PROMPT
    assert review.command_result.forward.runtime_mode is RuntimeMode.DEFAULT
    assert conversation.user_messages == []


@pytest.mark.asyncio
async def test_ui_commands_return_application_actions_or_streams() -> None:
    conversation = DummyConversation()

    exit_result = await _dispatch("/exit", conversation)
    compact_result = await _dispatch("/compact", conversation)
    clear_result = await _dispatch("/clear", conversation)
    resume_result = await _dispatch("/resume", conversation)

    assert exit_result.command_result.ui_action.value == "exit"  # type: ignore[union-attr]
    assert compact_result.command_result.stream is not None  # type: ignore[union-attr]
    assert clear_result.command_result.ui_action.value == "reset_session"  # type: ignore[union-attr]
    assert resume_result.command_result.ui_action.value == "select_session"  # type: ignore[union-attr]
