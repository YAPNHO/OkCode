"""Skill 系统的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from okcode.tools.models import PermissionTarget, ToolSafety


class SkillError(Exception):
    """Skill 系统的基类异常。"""


class SkillParseError(SkillError):
    """单个 Skill 文件解析失败。"""


class SkillArgumentError(SkillError):
    """LoadSkill 参数不能满足 SOP 占位符。"""


class SkillValidationError(SkillError):
    """Skill 全局校验失败。"""


class SkillSourceKind(StrEnum):
    """Skill 来源优先级。"""

    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"

    @property
    def priority(self) -> int:
        return {
            SkillSourceKind.BUILTIN: 0,
            SkillSourceKind.USER: 1,
            SkillSourceKind.PROJECT: 2,
        }[self]


class SkillExecutionMode(StrEnum):
    """Skill 执行模式。"""

    SHARED = "shared"
    ISOLATED = "isolated"


class SkillHistoryMode(StrEnum):
    """独立模式带入历史的范围。"""

    NONE = "none"
    RECENT = "recent"
    SUMMARY = "summary"
    ALL_SAFE = "all_safe"


@dataclass(frozen=True, slots=True)
class SkillToolManifest:
    """目录型 Skill 声明的专属工具。"""

    local_name: str
    exposed_name: str
    description: str
    schema_path: Path
    script_path: Path
    timeout_seconds: float
    safety: ToolSafety
    permission_target: PermissionTarget = field(default_factory=PermissionTarget)


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """启动期可见的 Skill 元数据。"""

    name: str
    description: str
    allowed_tools: tuple[str, ...]
    execution_mode: SkillExecutionMode
    history_mode: SkillHistoryMode
    model: str | None
    source: SkillSourceKind
    source_path: Path
    entry_path: Path
    package_dir: Path | None
    version_id: str
    has_body: bool
    dedicated_tools: tuple[SkillToolManifest, ...] = ()

    @property
    def key(self) -> str:
        return normalize_skill_name(self.name)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """LoadSkill 阶段读取的完整 Skill。"""

    metadata: SkillMetadata
    body: str
    placeholders: tuple[str, ...]
    dedicated_tools: tuple[SkillToolManifest, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillParseIssue:
    """单个 Skill 的非阻断解析问题。"""

    source_path: Path
    source: SkillSourceKind
    skill_name: str | None
    severity: Literal["warning", "error"]
    message: str

    def render(self) -> str:
        name = f"{self.skill_name}: " if self.skill_name else ""
        return f"{self.source.value}:{self.source_path} {name}{self.message}"


@dataclass(frozen=True, slots=True)
class SkillActivation:
    """已激活 Skill 的快照。"""

    name: str
    description: str
    source: SkillSourceKind
    source_path: Path
    version_id: str
    rendered_sop: str
    arguments: dict[str, object]
    allowed_tools: tuple[str, ...]
    exposed_dedicated_tool_names: tuple[str, ...]
    execution_mode: SkillExecutionMode
    history_mode: SkillHistoryMode
    model: str | None

    @property
    def key(self) -> str:
        return normalize_skill_name(self.name)


@dataclass(frozen=True, slots=True)
class SkillListEntryData:
    """`/skill` 列表中的一行。"""

    name: str
    description: str
    source: str
    active: bool
    version_id: str


def normalize_skill_name(name: str) -> str:
    """归一化 Skill 名称。"""

    return name.strip().lower()
