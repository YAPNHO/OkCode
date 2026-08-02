import pytest

from okcode.errors import ConfigError
from okcode.worktrees.naming import (
    derive_agent_branch_name,
    derive_agent_worktree_name,
    validate_worktree_name,
)


def test_validate_worktree_name_accepts_nested_safe_name() -> None:
    assert validate_worktree_name("agents/reviewer/task_123") == "agents/reviewer/task_123"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../escape",
        "agents/../escape",
        "agents//task",
        "agents/./task",
        "C:/temp",
        "/tmp/task",
        "agents\\task",
        "agents/task?",
        "a" * 161,
        "agents/" + "a" * 65,
    ],
)
def test_validate_worktree_name_rejects_unsafe_values(name: str) -> None:
    with pytest.raises(ConfigError):
        validate_worktree_name(name)


def test_derive_agent_names_are_stable_and_safe() -> None:
    name = derive_agent_worktree_name("Code Reviewer", "1234567890abcdef")

    assert name == "agents/code-reviewer/1234567890ab"
    assert derive_agent_branch_name(name) == "okcode/agents/agents/code-reviewer/1234567890ab"
