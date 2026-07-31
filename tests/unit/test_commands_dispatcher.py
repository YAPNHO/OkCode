from pathlib import Path

import pytest

from okcode.commands import (
    CommandContext,
    CommandDefinition,
    CommandDispatcher,
    CommandKind,
    CommandRegistry,
    CommandResult,
    DispatchResultKind,
)
from okcode.models import CommandNotice


@pytest.mark.asyncio
async def test_dispatcher_returns_empty_for_blank_input() -> None:
    dispatcher = CommandDispatcher(CommandRegistry(()))
    context = CommandContext(object(), CommandRegistry(()), object(), Path.cwd())  # type: ignore[arg-type]

    result = await dispatcher.dispatch("   ", context)

    assert result.kind is DispatchResultKind.EMPTY


@pytest.mark.asyncio
async def test_dispatcher_forwards_plain_text_without_calling_registry() -> None:
    registry = CommandRegistry(())
    dispatcher = CommandDispatcher(registry)
    context = CommandContext(object(), registry, object(), Path.cwd())  # type: ignore[arg-type]

    result = await dispatcher.dispatch("hello /help", context)

    assert result.kind is DispatchResultKind.TEXT
    assert result.text == "hello /help"


@pytest.mark.asyncio
async def test_dispatcher_guides_unknown_commands_to_help() -> None:
    registry = CommandRegistry(())
    dispatcher = CommandDispatcher(registry)
    context = CommandContext(object(), registry, object(), Path.cwd())  # type: ignore[arg-type]

    result = await dispatcher.dispatch("/missing arg", context)

    assert result.kind is DispatchResultKind.COMMAND
    assert result.command_result is not None
    assert isinstance(result.command_result.events[0], CommandNotice)
    assert "/help" in result.command_result.events[0].message


@pytest.mark.asyncio
async def test_dispatcher_invokes_matching_command_handler_with_parsed_args() -> None:
    seen: list[str] = []

    def handler(context: CommandContext, command: object) -> CommandResult:
        seen.append(command.args)  # type: ignore[attr-defined]
        return CommandResult(events=(CommandNotice("ok"),))

    registry = CommandRegistry(
        (
            CommandDefinition(
                "ping",
                ("p",),
                "ping description",
                "/ping",
                CommandKind.LOCAL,
                None,
                False,
                handler,
            ),
        )
    )
    dispatcher = CommandDispatcher(registry)
    context = CommandContext(object(), registry, object(), Path.cwd())  # type: ignore[arg-type]

    result = await dispatcher.dispatch("/P hello", context)

    assert result.kind is DispatchResultKind.COMMAND
    assert seen == ["hello"]
    assert result.command_result is not None
    assert result.command_result.events == (CommandNotice("ok"),)
