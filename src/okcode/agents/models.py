"""子 Agent 系统的领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from okcode.commands.models import RuntimeMode
from okcode.models import ChatMessage, TokenUsage
from okcode.permissions.models import PermissionMode
from okcode.worktrees.models import WorktreeExitReport, WorktreePrepareRequest


class AgentLaunchKind(StrEnum):
    """子 Agent 的启动类型。"""

    DEFINED = "defined"
    FORK = "fork"


class AgentExecutionMode(StrEnum):
    """子 Agent 的执行方式。"""

    FOREGROUND = "foreground"
    BACKGROUND = "background"
    AUTO = "auto"


class AgentTaskStatus(StrEnum):
    """后台任务生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    BACKGROUND = "background"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INCOMPLETE = "incomplete"


class AgentIsolationMode(StrEnum):
    """子 Agent 文件系统隔离模式。"""

    SHARED = "shared"
    WORKTREE = "worktree"


class AgentModelKind(StrEnum):
    """角色可声明的模型档位。"""

    INHERIT = "inherit"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class AgentPermissionKind(StrEnum):
    """角色可声明的权限策略。"""

    INHERIT = "inherit"
    DEFAULT = "default"
    STRICT = "strict"
    ALLOW = "allow"


class AgentRoleSourceKind(StrEnum):
    """角色定义来源，数值越大优先级越高。"""

    PLUGIN = "plugin"
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"

    @property
    def priority(self) -> int:
        return {
            AgentRoleSourceKind.PLUGIN: 0,
            AgentRoleSourceKind.BUILTIN: 1,
            AgentRoleSourceKind.USER: 2,
            AgentRoleSourceKind.PROJECT: 3,
        }[self]


@dataclass(frozen=True, slots=True)
class AgentModelPolicy:
    """角色的模型选择策略。"""

    kind: AgentModelKind = AgentModelKind.INHERIT
    resolved_model: str | None = None


@dataclass(frozen=True, slots=True)
class AgentPermissionPolicy:
    """角色的权限模式策略。"""

    kind: AgentPermissionKind = AgentPermissionKind.INHERIT
    resolved_mode: PermissionMode | None = None


@dataclass(frozen=True, slots=True)
class AgentRole:
    """一个已解析、已校验的子 Agent 角色。"""

    name: str
    description: str
    source_kind: AgentRoleSourceKind
    source_path: Path
    tool_allowlist: tuple[str, ...] = ()
    tool_denylist: tuple[str, ...] = ()
    model_policy: AgentModelPolicy = field(default_factory=AgentModelPolicy)
    max_turns: int = 6
    permission_policy: AgentPermissionPolicy = field(default_factory=AgentPermissionPolicy)
    system_prompt: str = ""
    isolation: AgentIsolationMode = AgentIsolationMode.SHARED


@dataclass(frozen=True, slots=True)
class AgentRoleListEntry:
    """角色列表展示项。"""

    name: str
    description: str
    source_kind: AgentRoleSourceKind
    source_path: Path
    shadowed_count: int = 0


@dataclass(frozen=True, slots=True)
class ShadowedAgentRole:
    """被更高优先级覆盖的角色记录。"""

    name: str
    shadowed: AgentRole
    effective: AgentRole


@dataclass(frozen=True, slots=True)
class AgentRoleCatalog:
    """最终生效角色和覆盖诊断。"""

    roles: dict[str, AgentRole]
    shadowed: tuple[ShadowedAgentRole, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def get(self, name: str) -> AgentRole:
        try:
            return self.roles[name]
        except KeyError as exc:
            raise LookupError(f"不存在名为 {name!r} 的子 Agent 角色。") from exc

    def list_entries(self) -> tuple[AgentRoleListEntry, ...]:
        counts: dict[str, int] = {}
        for item in self.shadowed:
            counts[item.name] = counts.get(item.name, 0) + 1
        return tuple(
            AgentRoleListEntry(
                role.name,
                role.description,
                role.source_kind,
                role.source_path,
                counts.get(role.name, 0),
            )
            for role in sorted(self.roles.values(), key=lambda value: value.name)
        )


@dataclass(frozen=True, slots=True)
class ParentAgentContext:
    """父 Agent 提供给子 Agent 启动器的隔离快照。"""

    session_id: str
    messages: tuple[ChatMessage, ...]
    runtime_mode: RuntimeMode
    permission_mode: PermissionMode
    visible_tool_names: tuple[str, ...]
    workspace_root: Path = field(default_factory=Path.cwd)
    depth: int = 0


@dataclass(frozen=True, slots=True)
class AgentToolRequest:
    """模型调用 agent 工具后的参数。"""

    kind: AgentLaunchKind
    task: str
    role: str | None = None
    background: bool = False
    timeout_seconds: float | None = None
    max_turns: int | None = None
    isolation: AgentIsolationMode | None = None
    worktree_name: str | None = None


@dataclass(frozen=True, slots=True)
class AgentLaunchRequest:
    """启动器内部使用的标准子 Agent 请求。"""

    task_id: str
    kind: AgentLaunchKind
    task: str
    parent_session_id: str
    role: AgentRole | None = None
    parent_messages: tuple[ChatMessage, ...] = ()
    parent_tool_names: tuple[str, ...] = ()
    visible_tool_names: tuple[str, ...] = ()
    tool_denied_reasons: dict[str, str] = field(default_factory=dict)
    execution_mode: AgentExecutionMode = AgentExecutionMode.FOREGROUND
    timeout_seconds: float | None = None
    max_turns: int = 6
    depth: int = 0
    trigger: str = "tool"
    runtime_mode: RuntimeMode = RuntimeMode.DEFAULT
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    isolation: AgentIsolationMode = AgentIsolationMode.SHARED
    worktree_request: WorktreePrepareRequest | None = None
    main_workspace_root: Path | None = None


@dataclass(frozen=True, slots=True)
class AgentToolPolicy:
    """子 Agent 可见工具的过滤输入。"""

    global_denied: tuple[str, ...] = ("agent",)
    background_allowed: tuple[str, ...] | None = None
    parent_allowed: tuple[str, ...] | None = None
    role_allowlist: tuple[str, ...] = ()
    role_denylist: tuple[str, ...] = ()
    depth: int = 0
    max_depth: int = 0


@dataclass(frozen=True, slots=True)
class AgentUsage:
    """子 Agent 独立用量汇总。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_request_count: int = 0
    tool_call_count: int = 0

    def add_token_usage(self, usage: TokenUsage) -> AgentUsage:
        return AgentUsage(
            input_tokens=self.input_tokens + (usage.input_tokens or 0),
            output_tokens=self.output_tokens + (usage.output_tokens or 0),
            total_tokens=self.total_tokens + (usage.total_tokens or 0),
            cache_read_tokens=self.cache_read_tokens + (usage.cache.read_tokens or 0),
            cache_write_tokens=self.cache_write_tokens + (usage.cache.write_tokens or 0),
            model_request_count=self.model_request_count + 1,
            tool_call_count=self.tool_call_count,
        )

    def add_tool_calls(self, count: int) -> AgentUsage:
        return AgentUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            model_request_count=self.model_request_count,
            tool_call_count=self.tool_call_count + count,
        )


@dataclass(frozen=True, slots=True)
class AgentTaskResult:
    """子 Agent 完成后的结果。"""

    task_id: str
    kind: AgentLaunchKind
    status: AgentTaskStatus
    role_name: str | None = None
    final_text: str = ""
    summary: str = ""
    full_result_ref: str | None = None
    error: str | None = None
    rounds: int = 0
    tool_calls: tuple[str, ...] = ()
    usage: AgentUsage = field(default_factory=AgentUsage)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    isolation: AgentIsolationMode = AgentIsolationMode.SHARED
    worktree: WorktreeExitReport | None = None


@dataclass(frozen=True, slots=True)
class AgentTaskSnapshot:
    """用户查询后台任务时看到的快照。"""

    task_id: str
    kind: AgentLaunchKind
    status: AgentTaskStatus
    role_name: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_seconds: float = 0.0
    rounds: int = 0
    tool_call_count: int = 0
    usage: AgentUsage = field(default_factory=AgentUsage)
    summary: str = ""
    error: str | None = None
    isolation: AgentIsolationMode = AgentIsolationMode.SHARED
    worktree: WorktreeExitReport | None = None


@dataclass(frozen=True, slots=True)
class AgentTaskNotification:
    """后台任务回到父对话的通知。"""

    parent_session_id: str
    result: AgentTaskResult
