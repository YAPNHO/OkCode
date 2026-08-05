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
        return self.root_for(scope) / "MEMORY.md"

    def legacy_index_for(self, scope: MemoryScope) -> Path:
        """返回旧版索引路径，仅用于兼容读取。"""

        return self.root_for(scope) / "index.md"

    def note_for(self, scope: MemoryScope, name: str) -> Path:
        validate_memory_name(name)
        return self.root_for(scope) / f"{name}.md"


@dataclass(frozen=True, slots=True)
class MemoryScopeUsage:
    """单个记忆范围的文件名和字节统计。"""

    files: tuple[str, ...]
    total_bytes: int


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """项目级和用户级记忆的文件快照。"""

    project: MemoryScopeUsage
    user: MemoryScopeUsage


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
    name: str | None = None
    summary: str = ""
    content: str = ""

    @property
    def note_ref(self) -> str | None:
        """旧字段兼容别名。"""

        return self.name

    @property
    def title(self) -> str:
        """旧字段兼容别名。"""

        return self.summary


@dataclass(frozen=True, slots=True)
class MemoryIndexEntry:
    """索引中指向一条笔记的精简摘要。"""

    name: str
    category: MemoryCategory
    summary: str

    @property
    def note_ref(self) -> str:
        """旧字段兼容别名。"""

        return self.name


@dataclass(frozen=True, slots=True)
class MemoryUpdate:
    """单次 LLM 响应产生的笔记操作和两份完整候选索引。"""

    operations: tuple[MemoryOperation, ...]
    user_index: tuple[MemoryIndexEntry, ...]
    project_index: tuple[MemoryIndexEntry, ...]


_MEMORY_NAME_INVALID_CHARS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
_INDEX_RESERVED_NAMES = frozenset({"memory", "index"})


def validate_memory_name(name: str) -> None:
    """校验可作为 Windows Markdown 文件名的记忆名称。"""

    if not isinstance(name, str) or not name.strip():
        raise ValueError("记忆名称不能为空。")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ValueError("记忆名称不能包含控制字符。")
    if any(character in _MEMORY_NAME_INVALID_CHARS for character in name):
        raise ValueError("记忆名称包含 Windows 不允许的字符。")
    if name[-1] in {" ", "."}:
        raise ValueError("记忆名称不能以空格或句点结尾。")
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise ValueError("记忆名称不能使用 Windows 保留设备名。")
    if name.casefold() in _INDEX_RESERVED_NAMES:
        raise ValueError("记忆名称不能覆盖索引文件。")
