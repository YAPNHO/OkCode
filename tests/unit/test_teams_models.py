from __future__ import annotations

from pathlib import Path

import pytest

from okcode.teams.models import TeamMessage, TeamMessageProtocol
from okcode.teams.naming import validate_member_name, validate_team_name
from okcode.teams.paths import TeamPaths, default_teams_root


@pytest.mark.parametrize("name", ["core", "core-team", "core_team", "core.team", "a1"])
def test_team_and_member_names_accept_safe_values(name: str) -> None:
    assert validate_team_name(name) == name
    assert validate_member_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "../core", "core/name", r"core\name", "C:core", "bad\nname"],
)
def test_team_and_member_names_reject_path_like_values(name: str) -> None:
    with pytest.raises(ValueError):
        validate_team_name(name)
    with pytest.raises(ValueError):
        validate_member_name(name)


def test_default_teams_root_uses_current_project() -> None:
    assert default_teams_root() == Path.cwd() / ".okcode" / "team"


def test_team_paths_stay_under_root(tmp_path: Path) -> None:
    paths = TeamPaths.for_team(tmp_path, "core")

    assert paths.root == (tmp_path / "core").resolve()
    assert paths.team_json == paths.root / "team.json"
    assert paths.members_json == paths.root / "members.json"
    assert paths.tasks_json == paths.root / "tasks.json"
    assert paths.registry_json == paths.root / "registry.json"
    assert paths.mailbox_path("worker") == paths.mailboxes_dir / "worker.jsonl"


def test_message_model_defaults_to_text_protocol() -> None:
    message = TeamMessage("lead", "worker", "hello")

    assert message.protocol is TeamMessageProtocol.TEXT
    assert message.read is False
    assert message.payload == {}
