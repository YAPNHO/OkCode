"""团队元数据、成员、注册表和共享任务持久化。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from okcode.teams.locking import FileLock
from okcode.teams.models import (
    MemberContextRef,
    NameRegistry,
    NameRegistryEntry,
    TeamMember,
    TeamMemberStatus,
    TeamMetadata,
    TeamSnapshot,
    TeamTask,
    utc_now,
)
from okcode.teams.naming import validate_member_name, validate_team_name
from okcode.teams.paths import TeamPaths, default_teams_root
from okcode.teams.serialization import coerce_dataclass, read_json, write_json_atomic


class TeamStore:
    """团队状态的磁盘持久化入口。"""

    def __init__(
        self,
        teams_root: Path | None = None,
        *,
        lock_timeout_seconds: float = 5.0,
        stale_lock_seconds: float = 30.0,
    ) -> None:
        self._teams_root = (teams_root or default_teams_root()).resolve()
        self._lock_timeout = lock_timeout_seconds
        self._stale_lock = stale_lock_seconds

    @property
    def teams_root(self) -> Path:
        return self._teams_root

    def paths(self, team_name: str) -> TeamPaths:
        return TeamPaths.for_team(self._teams_root, team_name)

    def create(self, metadata: TeamMetadata) -> TeamSnapshot:
        safe_name = validate_team_name(metadata.name)
        paths = self.paths(safe_name)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.mailboxes_dir.mkdir(parents=True, exist_ok=True)
        paths.member_sessions_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        stored = replace(
            metadata,
            name=safe_name,
            root_path=paths.root,
            created_at=metadata.created_at or now,
            updated_at=now,
        )
        write_json_atomic(paths.team_json, stored)
        if not paths.members_json.exists():
            write_json_atomic(paths.members_json, [])
        if not paths.tasks_json.exists():
            write_json_atomic(paths.tasks_json, [])
        if not paths.registry_json.exists():
            write_json_atomic(paths.registry_json, {"entries": []})
        return self.load(safe_name)

    def load(self, team_name: str) -> TeamSnapshot:
        paths = self.paths(team_name)
        raw_metadata = read_json(paths.team_json, None)
        if not isinstance(raw_metadata, dict):
            raise FileNotFoundError(f"未找到团队：{team_name}")
        metadata = coerce_dataclass(TeamMetadata, raw_metadata)
        members = tuple(
            coerce_dataclass(TeamMember, item)
            for item in _ensure_list(read_json(paths.members_json, []))
        )
        tasks = tuple(
            coerce_dataclass(TeamTask, item)
            for item in _ensure_list(read_json(paths.tasks_json, []))
        )
        unread_counts = {member.name: _count_unread(member.mailbox_path) for member in members}
        recoverable = {
            member.name: member.context_ref is not None and member.workdir.exists()
            for member in members
        }
        return TeamSnapshot(metadata, members, tasks, unread_counts, recoverable)

    def upsert_member(self, team_name: str, member: TeamMember) -> TeamMember:
        safe_member = validate_member_name(member.name)
        paths = self.paths(team_name)
        mailbox_path = paths.mailbox_path(safe_member)
        stored_member = replace(member, name=safe_member, mailbox_path=mailbox_path)

        def mutate(members: list[TeamMember]) -> list[TeamMember]:
            by_name = {item.name: item for item in members}
            by_name[safe_member] = stored_member
            return list(by_name.values())

        self._mutate_members(team_name, mutate)
        self.update_registry(
            team_name,
            NameRegistryEntry(
                safe_member,
                mailbox_path,
                stored_member.backend,
                stored_member.status,
                stored_member.backend_handle,
                stored_member.last_active_at,
            ),
        )
        mailbox_path.parent.mkdir(parents=True, exist_ok=True)
        mailbox_path.touch(exist_ok=True)
        return stored_member

    def update_member_status(
        self,
        team_name: str,
        member_name: str,
        status: TeamMemberStatus,
        context_ref: MemberContextRef | None = None,
        error: str | None = None,
    ) -> TeamMember:
        safe_member = validate_member_name(member_name)
        updated: TeamMember | None = None

        def mutate(members: list[TeamMember]) -> list[TeamMember]:
            nonlocal updated
            result = []
            for member in members:
                if member.name == safe_member:
                    updated = replace(
                        member,
                        status=status,
                        context_ref=context_ref if context_ref is not None else member.context_ref,
                        last_error=error,
                        last_active_at=utc_now(),
                    )
                    result.append(updated)
                else:
                    result.append(member)
            return result

        self._mutate_members(team_name, mutate)
        if updated is None:
            raise LookupError(f"成员不存在：{safe_member}")
        self.update_registry(
            team_name,
            NameRegistryEntry(
                updated.name,
                updated.mailbox_path,
                updated.backend,
                updated.status,
                updated.backend_handle,
                updated.last_active_at,
            ),
        )
        return updated

    def read_registry(self, team_name: str) -> NameRegistry:
        paths = self.paths(team_name)
        raw = read_json(paths.registry_json, {"entries": []})
        if not isinstance(raw, dict):
            return NameRegistry()
        entries = tuple(
            coerce_dataclass(NameRegistryEntry, item)
            for item in _ensure_list(raw.get("entries", []))
        )
        return NameRegistry(entries)

    def update_registry(self, team_name: str, entry: NameRegistryEntry) -> NameRegistry:
        paths = self.paths(team_name)
        lease = FileLock.acquire(
            paths.registry_json.with_suffix(".lock"),
            timeout_seconds=self._lock_timeout,
            stale_seconds=self._stale_lock,
            owner=f"registry:{team_name}",
        )
        try:
            registry = self.read_registry(team_name)
            entries = {item.name: item for item in registry.entries}
            entries[entry.name] = entry
            updated = NameRegistry(tuple(entries.values()))
            write_json_atomic(paths.registry_json, {"entries": updated.entries})
            return updated
        finally:
            FileLock.release(lease)

    def list_tasks(self, team_name: str) -> tuple[TeamTask, ...]:
        paths = self.paths(team_name)
        return tuple(
            coerce_dataclass(TeamTask, item)
            for item in _ensure_list(read_json(paths.tasks_json, []))
        )

    def mutate_tasks(
        self,
        team_name: str,
        mutator: Callable[[list[TeamTask]], list[TeamTask]],
    ) -> tuple[TeamTask, ...]:
        paths = self.paths(team_name)
        lease = FileLock.acquire(
            paths.tasks_json.with_suffix(".lock"),
            timeout_seconds=self._lock_timeout,
            stale_seconds=self._stale_lock,
            owner=f"tasks:{team_name}",
        )
        try:
            tasks = list(self.list_tasks(team_name))
            updated = mutator(tasks)
            ids = [task.task_id for task in updated]
            if len(ids) != len(set(ids)):
                raise ValueError("共享任务 task_id 不能重复。")
            write_json_atomic(paths.tasks_json, updated)
            self._touch_metadata(team_name)
            return tuple(updated)
        finally:
            FileLock.release(lease)

    def new_task_id(self) -> str:
        return f"task-{uuid.uuid4().hex[:12]}"

    def _mutate_members(
        self,
        team_name: str,
        mutator: Callable[[list[TeamMember]], list[TeamMember]],
    ) -> tuple[TeamMember, ...]:
        paths = self.paths(team_name)
        lease = FileLock.acquire(
            paths.members_json.with_suffix(".lock"),
            timeout_seconds=self._lock_timeout,
            stale_seconds=self._stale_lock,
            owner=f"members:{team_name}",
        )
        try:
            members = [
                coerce_dataclass(TeamMember, item)
                for item in _ensure_list(read_json(paths.members_json, []))
            ]
            updated = mutator(members)
            names = [member.name for member in updated]
            if len(names) != len(set(names)):
                raise ValueError("成员名称不能重复。")
            write_json_atomic(paths.members_json, updated)
            self._touch_metadata(team_name)
            return tuple(updated)
        finally:
            FileLock.release(lease)

    def _touch_metadata(self, team_name: str) -> None:
        paths = self.paths(team_name)
        raw = read_json(paths.team_json, None)
        if not isinstance(raw, dict):
            return
        metadata = coerce_dataclass(TeamMetadata, raw)
        write_json_atomic(paths.team_json, replace(metadata, updated_at=utc_now()))


def _ensure_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _count_unread(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and '"read":true' not in line.replace(" ", "").lower():
            count += 1
    return count
