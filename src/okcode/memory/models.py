"""长期记忆的范围、分类和受控更新模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from okcode.models import ChatMessage


class MemoryScope(StrEnum):
    """笔记的可见范围。"""

    USER = "user"
    PROJECT = "project"


class MemoryCategory(StrEnum):
    """自动笔记的固定分类。"""

    PREFERENCE = "preference"
    CORRECTION = "correction"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE = "reference"


class MemoryAction(StrEnum):
    """LLM 可请求的唯一记忆更新操作。"""

    CREATE = "create"
    APPEND = "append"
    NOOP = "noop"


@dataclass(frozen=True, slots=True)
class MemoryPaths:
    """项目级和用户级记忆的根目录。"""

    project_root: Path
    user_root: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> MemoryPaths:
        """返回当前项目和当前用户的记忆目录。"""

        root = workspace_root / ".okcode" / "memory"
        return cls(root / "project", root / "user")

    def root_for(self, scope: MemoryScope) -> Path:
        return self.user_root if scope is MemoryScope.USER else self.project_root

    def index_for(self, scope: MemoryScope) -> Path:
        return self.root_for(scope) / "index.md"

    def note_for(self, scope: MemoryScope, note_ref: str) -> Path:
        return self.root_for(scope) / f"{note_ref}.md"


@dataclass(frozen=True, slots=True)
class MemoryJob:
    """一次自然结束后交给后台线程的完整本轮消息。"""

    messages: tuple[ChatMessage, ...]


@dataclass(frozen=True, slots=True)
class MemoryOperation:
    """LLM 对单条笔记提出的受控变更。"""

    scope: MemoryScope
    category: MemoryCategory
    action: MemoryAction
    note_ref: str | None = None
    title: str = ""
    content: str = ""


@dataclass(frozen=True, slots=True)
class MemoryIndexEntry:
    """索引中指向一条笔记的精简摘要。"""

    note_ref: str
    category: MemoryCategory
    summary: str


@dataclass(frozen=True, slots=True)
class MemoryUpdate:
    """单次 LLM 响应产生的笔记操作和两份完整候选索引。"""

    operations: tuple[MemoryOperation, ...]
    user_index: tuple[MemoryIndexEntry, ...]
    project_index: tuple[MemoryIndexEntry, ...]
