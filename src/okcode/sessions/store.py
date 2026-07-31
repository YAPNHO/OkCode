"""JSONL 会话追加、扫描、恢复和过期清理。"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from okcode.models import ChatMessage, Role
from okcode.sessions.codec import complete_message_prefix, decode_record, encode_record
from okcode.sessions.models import RecoveredSession, SessionConfig, SessionDescriptor, StoredMessage

_SESSION_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_TITLE_LIMIT = 80


class SessionJournal:
    """一个惰性创建的会话 JSONL 追加器。"""

    def __init__(self, session_id: str, path: Path, clock: Callable[[], datetime]) -> None:
        self.session_id = session_id
        self.path = path
        self._clock = clock
        self._closed = False

    def close(self) -> None:
        """停止继续使用当前追加器。"""

        self._closed = True

    def append(self, messages: Sequence[ChatMessage]) -> None:
        """在首次成功提交后逐行追加完整消息。"""

        if self._closed:
            raise OSError("会话存档已关闭。")
        if not messages:
            return
        timestamp = _as_utc(self._clock())
        rows = [encode_record(timestamp, message) for message in messages]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            for row in rows:
                file.write(row + "\n")
            file.flush()
            os.fsync(file.fileno())


class SessionStore:
    """当前项目内会话日志的唯一读写入口。"""

    def __init__(
        self,
        workspace_root: Path,
        config: SessionConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve(strict=True)
        self._sessions_dir = self._workspace_root / "sessions"
        self.config = config or SessionConfig()
        self._clock = clock or _utc_now
        self._token_factory = token_factory or _session_token

    def create_journal(self) -> SessionJournal:
        """创建尚未写入磁盘的新会话日志句柄。"""

        timestamp = _as_utc(self._clock())
        for _ in range(100):
            session_id = f"{timestamp:%Y%m%d-%H%M%S}-{self._token_factory()}"
            if not _SESSION_ID_PATTERN.fullmatch(session_id):
                raise ValueError("会话随机后缀必须是四位小写十六进制。")
            path = self._sessions_dir / f"{session_id}.jsonl"
            if not path.exists():
                return SessionJournal(session_id, path, self._clock)
        raise RuntimeError("无法生成不重复的会话 ID。")

    def list_resumable(self) -> tuple[SessionDescriptor, ...]:
        """清理过期文件后，直接扫描可恢复会话摘要。"""

        self.cleanup_expired()
        descriptors = []
        for path in self._session_paths():
            loaded = self._load(path)
            if not loaded.records:
                continue
            messages, _ = complete_message_prefix(tuple(item.message for item in loaded.records))
            if not messages:
                continue
            descriptors.append(
                SessionDescriptor(
                    id=path.stem,
                    title=_title(messages),
                    message_count=len(messages),
                    updated_at=max(item.timestamp for item in loaded.records),
                )
            )
        return tuple(sorted(descriptors, key=lambda item: item.updated_at, reverse=True))

    def restore(self, session_id: str) -> RecoveredSession:
        """恢复指定日志的可用前缀，绝不接受工作区外路径。"""

        if not _SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("会话 ID 格式无效。")
        path = self._sessions_dir / f"{session_id}.jsonl"
        if not path.is_file():
            raise ValueError("会话不存在或已过期。")
        loaded = self._load(path)
        if not loaded.records:
            raise ValueError("会话没有可恢复的消息。")
        messages, was_truncated = complete_message_prefix(
            tuple(item.message for item in loaded.records)
        )
        if not messages:
            raise ValueError("会话没有完整的可恢复消息。")
        return RecoveredSession(
            messages=messages,
            updated_at=max(item.timestamp for item in loaded.records),
            skipped_lines=loaded.skipped_lines,
            was_truncated=was_truncated,
        )

    def journal_for(self, session_id: str) -> SessionJournal:
        """返回一个已有会话的追加日志句柄。"""

        if not _SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("会话 ID 格式无效。")
        path = self._sessions_dir / f"{session_id}.jsonl"
        if not path.is_file():
            raise ValueError("会话不存在或已过期。")
        return SessionJournal(session_id, path, self._clock)

    def is_long_gap(self, updated_at: datetime) -> bool:
        """判断恢复会话距上次活动是否超过提醒阈值。"""

        return _as_utc(self._clock()) - _as_utc(updated_at) > self.config.long_gap

    def cleanup_expired(self, now: datetime | None = None) -> int:
        """删除文件修改时间超过保留期的 JSONL 日志。"""

        current = _as_utc(now or self._clock())
        threshold = current - timedelta(days=self.config.retention_days)
        deleted = 0
        for path in self._session_paths():
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified < threshold:
                path.unlink()
                deleted += 1
        return deleted

    def _session_paths(self) -> tuple[Path, ...]:
        if not self._sessions_dir.is_dir():
            return ()
        return tuple(
            path
            for path in self._sessions_dir.glob("*.jsonl")
            if _SESSION_ID_PATTERN.fullmatch(path.stem) and path.is_file()
        )

    @staticmethod
    def _load(path: Path) -> _LoadedSession:
        records: list[StoredMessage] = []
        skipped_lines = 0
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"无法读取会话：{path.name}") from exc
        for line in lines:
            if not line.strip():
                skipped_lines += 1
                continue
            try:
                records.append(decode_record(line))
            except ValueError:
                skipped_lines += 1
        return _LoadedSession(tuple(records), skipped_lines)


class _LoadedSession:
    def __init__(self, records: tuple[StoredMessage, ...], skipped_lines: int) -> None:
        self.records = records
        self.skipped_lines = skipped_lines


def _title(messages: Sequence[ChatMessage]) -> str:
    for message in messages:
        if message.role is Role.USER:
            title = " ".join(message.content.split())
            if len(title) > _TITLE_LIMIT:
                return title[:_TITLE_LIMIT] + "..."
            return title or "（无标题）"
    return "（无标题）"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _session_token() -> str:
    return secrets.token_hex(2)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("会话时间必须包含时区。")
    return value.astimezone(UTC)
