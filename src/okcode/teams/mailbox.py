"""成员邮箱 JSONL 协议。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from okcode.teams.locking import FileLock
from okcode.teams.models import (
    BroadcastReport,
    MessageDeliveryReport,
    TeamMessage,
    utc_now,
)
from okcode.teams.serialization import coerce_dataclass, to_jsonable


class MailboxFormatError(ValueError):
    """邮箱文件无法解析。"""


class MailboxStore:
    """邮箱追加、读取和标记已读。"""

    def __init__(
        self,
        *,
        lock_timeout_seconds: float = 5.0,
        stale_lock_seconds: float = 30.0,
    ) -> None:
        self._lock_timeout = lock_timeout_seconds
        self._stale_lock = stale_lock_seconds

    def append(self, mailbox_path: Path, message: TeamMessage) -> TeamMessage:
        mailbox_path.parent.mkdir(parents=True, exist_ok=True)
        lease = FileLock.acquire(
            mailbox_path.with_suffix(mailbox_path.suffix + ".lock"),
            timeout_seconds=self._lock_timeout,
            stale_seconds=self._stale_lock,
            owner=f"mailbox:{message.recipient}",
        )
        try:
            stored = _normalize_message(message)
            with mailbox_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(to_jsonable(stored), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            return stored
        finally:
            FileLock.release(lease)

    def append_many(
        self,
        targets: tuple[Path, ...],
        message_factory: Callable[[Path], TeamMessage],
    ) -> BroadcastReport:
        results: list[MessageDeliveryReport] = []
        for target in targets:
            message = message_factory(target)
            try:
                stored = self.append(target, message)
            except Exception as exc:
                results.append(
                    MessageDeliveryReport(
                        recipient=message.recipient,
                        status="failed",
                        mailbox_path=target,
                        error=str(exc),
                    )
                )
                continue
            results.append(
                MessageDeliveryReport(
                    recipient=stored.recipient,
                    status="delivered",
                    message_id=stored.message_id,
                    mailbox_path=target,
                )
            )
        return BroadcastReport(tuple(results))

    def unread(self, mailbox_path: Path) -> tuple[TeamMessage, ...]:
        return tuple(message for message in self._read_all(mailbox_path) if not message.read)

    def mark_read(
        self,
        mailbox_path: Path,
        message_ids: tuple[str, ...],
    ) -> tuple[TeamMessage, ...]:
        ids = set(message_ids)
        lease = FileLock.acquire(
            mailbox_path.with_suffix(mailbox_path.suffix + ".lock"),
            timeout_seconds=self._lock_timeout,
            stale_seconds=self._stale_lock,
            owner=f"mailbox-read:{mailbox_path.name}",
        )
        try:
            messages = [
                replace(message, read=True) if message.message_id in ids else message
                for message in self._read_all(mailbox_path)
            ]
            mailbox_path.parent.mkdir(parents=True, exist_ok=True)
            mailbox_path.write_text(
                "".join(
                    json.dumps(to_jsonable(message), ensure_ascii=False, sort_keys=True) + "\n"
                    for message in messages
                ),
                encoding="utf-8",
            )
            return tuple(messages)
        finally:
            FileLock.release(lease)

    def _read_all(self, mailbox_path: Path) -> tuple[TeamMessage, ...]:
        if not mailbox_path.exists():
            return ()
        messages: list[TeamMessage] = []
        for index, line in enumerate(mailbox_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("消息行必须是对象。")
                messages.append(coerce_dataclass(TeamMessage, raw))
            except Exception as exc:
                raise MailboxFormatError(f"{mailbox_path}:{index} 邮箱消息无法解析：{exc}") from exc
        return tuple(messages)


def _normalize_message(message: TeamMessage) -> TeamMessage:
    created = message.created_at or utc_now()
    summary = message.summary or _summary(message.body)
    message_id = message.message_id or f"msg-{uuid.uuid4().hex[:12]}"
    return replace(
        message,
        message_id=message_id,
        created_at=created,
        read=message.read,
        summary=summary,
    )


def _summary(body: str) -> str:
    compact = " ".join(body.split())
    if len(compact) <= 120:
        return compact
    return compact[:117] + "..."
