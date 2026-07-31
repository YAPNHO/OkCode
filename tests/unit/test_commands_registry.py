import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from okcode.commands import (
    CommandContext,
    CommandDefinition,
    CommandKind,
    CommandRegistry,
    CommandResult,
)
from okcode.commands.completion import SlashCommandCompleter


def _handler(context: CommandContext, command: object) -> CommandResult:
    return CommandResult()


def _command(
    name: str,
    aliases: tuple[str, ...] = (),
    *,
    hidden: bool = False,
) -> CommandDefinition:
    return CommandDefinition(
        name=name,
        aliases=aliases,
        description=f"{name} description",
        usage=f"/{name}",
        kind=CommandKind.LOCAL,
        argument_hint=None,
        hidden=hidden,
        handler=_handler,
    )


def test_registry_resolves_names_and_aliases_case_insensitively() -> None:
    registry = CommandRegistry((_command("status", ("st",)),))

    assert registry.resolve("STATUS") is registry.resolve("status")
    assert registry.resolve("/St") is registry.resolve("status")


def test_registry_rejects_name_and_alias_conflicts_at_startup() -> None:
    with pytest.raises(ValueError, match="冲突|conflict"):
        CommandRegistry((_command("status"), _command("other", ("STATUS",))))


def test_visible_commands_are_sorted_and_hidden_commands_are_excluded() -> None:
    registry = CommandRegistry(
        (_command("zeta"), _command("alpha"), _command("secret", hidden=True))
    )

    assert [command.name for command in registry.visible_commands()] == ["alpha", "zeta"]


def test_completion_candidates_include_aliases_but_exclude_hidden_commands() -> None:
    registry = CommandRegistry(
        (
            _command("help", ("h",)),
            _command("hidden", hidden=True),
            _command("status"),
        )
    )

    assert [candidate.text for candidate in registry.completion_candidates("h")] == [
        "/h",
        "/help",
    ]


def test_slash_completer_only_completes_command_name_position() -> None:
    registry = CommandRegistry((_command("help"), _command("status")))
    completer = SlashCommandCompleter(registry)
    event = CompleteEvent(completion_requested=True)

    candidates = list(completer.get_completions(Document("/he"), event))
    after_space = list(completer.get_completions(Document("/help "), event))

    assert [candidate.text for candidate in candidates] == ["/help"]
    assert after_space == []
