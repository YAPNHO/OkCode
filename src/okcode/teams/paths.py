"""团队持久化路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from okcode.teams.naming import validate_team_name


def default_teams_root() -> Path:
    """返回当前项目目录下的默认团队根目录。"""

    return Path.cwd() / ".okcode" / "team"


@dataclass(frozen=True, slots=True)
class TeamPaths:
    """单个团队的持久化文件布局。"""

    root: Path
    team_json: Path
    members_json: Path
    tasks_json: Path
    registry_json: Path
    mailboxes_dir: Path
    member_sessions_dir: Path

    @classmethod
    def for_team(cls, teams_root: Path, team_name: str) -> TeamPaths:
        safe_name = validate_team_name(team_name)
        root_base = teams_root.resolve()
        root = (root_base / safe_name).resolve()
        try:
            root.relative_to(root_base)
        except ValueError as exc:
            raise ValueError("团队目录必须位于 teams_root 内。") from exc
        return cls(
            root=root,
            team_json=root / "team.json",
            members_json=root / "members.json",
            tasks_json=root / "tasks.json",
            registry_json=root / "registry.json",
            mailboxes_dir=root / "mailboxes",
            member_sessions_dir=root / "member-sessions",
        )

    def mailbox_path(self, member_name: str) -> Path:
        from okcode.teams.naming import validate_member_name

        return self.mailboxes_dir / f"{validate_member_name(member_name)}.jsonl"
