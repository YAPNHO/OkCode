from okcode.commands.parser import CommandParser


def test_parser_returns_empty_for_blank_input() -> None:
    result = CommandParser().parse("  \t ")

    assert result.empty is True
    assert result.is_command is False
    assert result.command is None


def test_parser_leaves_non_command_text_unchanged() -> None:
    result = CommandParser().parse("hello /help")

    assert result.empty is False
    assert result.is_command is False
    assert result.text == "hello /help"


def test_parser_extracts_case_insensitive_command_and_arguments() -> None:
    raw = "  /HeLP   topic one  "

    result = CommandParser().parse(raw)

    assert result.empty is False
    assert result.is_command is True
    assert result.command is not None
    assert result.command.raw == raw
    assert result.command.name == "help"
    assert result.command.args == "topic one"
