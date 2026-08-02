"""团队通知到 SystemInstruction 的桥接。"""

from __future__ import annotations

from okcode.prompt import SystemInstruction
from okcode.teams.mailbox import MailboxStore
from okcode.teams.store import TeamStore


class TeamNotificationBridge:
    """把团队未读消息压缩成模型可见系统指令。"""

    def __init__(self, store: TeamStore, mailbox: MailboxStore) -> None:
        self._store = store
        self._mailbox = mailbox

    def instructions_for(self, team_name: str, actor_name: str) -> tuple[SystemInstruction, ...]:
        registry = self._store.read_registry(team_name)
        entry = registry.get(actor_name)
        if entry is None:
            return ()
        messages = self._mailbox.unread(entry.mailbox_path)
        if not messages:
            return ()
        lines = [
            f"- {message.message_id} from {message.sender}: {message.summary or message.body}"
            for message in messages[:10]
        ]
        content = "你有未读团队消息：\n" + "\n".join(lines)
        return (SystemInstruction("team_messages", content, priority=108),)
