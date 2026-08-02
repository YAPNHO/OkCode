"""长期团队协作的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from okcode.tools.models import JSONValue


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(UTC)


class TeamStatus(StrEnum):
    """小组状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"
    FAILED = "failed"


class TeamMemberStatus(StrEnum):
    """长期成员生命周期状态。"""

    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"
    UNRECOVERABLE = "unrecoverable"


class TeamTaskStatus(StrEnum):
    """共享任务状态。"""

    TODO = "todo"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TeamBackendKind(StrEnum):
    """成员运行后端。"""

    TERMINAL_PANE = "terminal_pane"
    COROUTINE = "coroutine"


class TeamMessageProtocol(StrEnum):
    """邮箱中的结构化消息协议。"""

    TEXT = "text"
    TASK_ASSIGNMENT = "task_assignment"
    TASK_STATUS = "task_status"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_DECISION = "approval_decision"
    BLOCKED = "blocked"
    COMPLETION = "completion"
    RESUME = "resume"
    BROADCAST = "broadcast"


class TeamActorKind(StrEnum):
    """团队工具调用者身份。"""

    LEAD = "lead"
    MEMBER = "member"


class TeamMergeStrategy(StrEnum):
    """多成员合并策略。"""

    SEQUENTIAL = "sequential"


class TeamMergeStatus(StrEnum):
    """多成员合并结果。"""

    CLEAN = "clean"
    AUTO_RESOLVED = "auto_resolved"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TeamMetadata:
    """一个长期小组的持久元数据。"""

    version: int
    name: str
    leader_session_id: str
    root_path: Path
    status: TeamStatus = TeamStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TeamBackendHandle:
    """成员后端恢复和唤醒所需的稳定信息。"""

    kind: TeamBackendKind
    identifier: str
    cwd: Path | None = None
    data: dict[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemberContextRef:
    """成员最近一次可恢复上下文引用。"""

    session_id: str
    journal_path: Path
    workspace_root: Path
    backend_kind: TeamBackendKind
    last_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class TeamMember:
    """团队成员花名册记录。"""

    name: str
    role: str
    workdir: Path
    backend: TeamBackendKind
    mailbox_path: Path
    approval_required: bool = False
    status: TeamMemberStatus = TeamMemberStatus.IDLE
    backend_handle: TeamBackendHandle | None = None
    context_ref: MemberContextRef | None = None
    last_active_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TeamTask:
    """共享任务列表中的一条任务。"""

    task_id: str
    title: str
    body: str
    owner: str | None = None
    status: TeamTaskStatus = TeamTaskStatus.TODO
    dependencies: tuple[str, ...] = ()
    blocked_reason: str | None = None
    output_summary: str | None = None
    related_messages: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TeamMessage:
    """成员邮箱中的一条消息。"""

    sender: str
    recipient: str
    body: str
    protocol: TeamMessageProtocol = TeamMessageProtocol.TEXT
    message_id: str = ""
    summary: str = ""
    created_at: datetime | None = None
    read: bool = False
    task_id: str | None = None
    payload: dict[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """成员发给 Lead 的计划审批请求。"""

    request_id: str
    member_name: str
    task_id: str
    plan: str
    risk_summary: str
    requested_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Lead 发给成员的结构化审批结果。"""

    request_id: str
    approved: bool
    reason: str
    constraints: tuple[str, ...] = ()
    decided_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class NameRegistryEntry:
    """名称注册表中的一个成员入口。"""

    name: str
    mailbox_path: Path
    backend: TeamBackendKind
    status: TeamMemberStatus
    backend_handle: TeamBackendHandle | None = None
    last_active_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NameRegistry:
    """小组内成员名到邮箱和后端唤醒信息的映射。"""

    entries: tuple[NameRegistryEntry, ...] = ()

    def get(self, name: str) -> NameRegistryEntry | None:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None


@dataclass(frozen=True, slots=True)
class TeamSnapshot:
    """用户或模型可见的小组状态快照。"""

    metadata: TeamMetadata
    members: tuple[TeamMember, ...] = ()
    tasks: tuple[TeamTask, ...] = ()
    unread_counts: dict[str, int] = field(default_factory=dict)
    recoverable: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeamToolContext:
    """团队工具调用时必须存在的上下文。"""

    team_name: str
    actor_name: str
    actor_kind: TeamActorKind
    coordinator: bool = False
    allowed_team_actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MessageDeliveryReport:
    """一次点对点消息发送结果。"""

    recipient: str
    status: str
    message_id: str | None = None
    mailbox_path: Path | None = None
    error: str | None = None
    woken: bool = False


@dataclass(frozen=True, slots=True)
class BroadcastReport:
    """广播消息对每个目标的发送结果。"""

    results: tuple[MessageDeliveryReport, ...]


@dataclass(frozen=True, slots=True)
class BackendPreference:
    """成员后端选择请求。"""

    required_kind: TeamBackendKind | None = None
    require_strong_isolation: bool = False
    allow_auto: bool = True


@dataclass(frozen=True, slots=True)
class BackendCapability:
    """成员后端能力检测结果。"""

    kind: TeamBackendKind
    available: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class BackendSelection:
    """后端选择器返回的具体选择。"""

    kind: TeamBackendKind
    reason: str
    capability: BackendCapability


@dataclass(frozen=True, slots=True)
class TeamMergeRequest:
    """Lead 发起的多成员合并请求。"""

    team_name: str
    member_names: tuple[str, ...]
    target_workspace: Path
    strategy: TeamMergeStrategy = TeamMergeStrategy.SEQUENTIAL


@dataclass(frozen=True, slots=True)
class TeamMergeReport:
    """多成员合并报告。"""

    status: TeamMergeStatus
    merged_members: tuple[str, ...] = ()
    skipped_members: tuple[str, ...] = ()
    conflict_files: tuple[str, ...] = ()
    rollback_performed: bool = False
    message: str = ""
    source_refs: tuple[str, ...] = ()
