"""会话存档的领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from okcode.models import ChatMessage


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """会话存档的保留和恢复提示阈值。"""

    retention_days: int = 30
    long_gap: timedelta = timedelta(hours=24)

    def __post_init__(self) -> None:
        if self.retention_days < 1:
            raise ValueError("会话保留天数必须为正数。")
        if self.long_gap <= timedelta():
            raise ValueError("会话时间间隔阈值必须为正数。")


@dataclass(frozen=True, slots=True)
class SessionDescriptor:
    """直接扫描 JSONL 得到、可显示给用户的会话摘要。"""

    id: str
    title: str
    message_count: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveredSession:
    """从 JSONL 有效前缀恢复得到的会话。"""

    messages: tuple[ChatMessage, ...]
    updated_at: datetime
    skipped_lines: int
    was_truncated: bool


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """一条带持久化时间的协议无关消息。"""

    timestamp: datetime
    message: ChatMessage
