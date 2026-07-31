"""Hook 机制的领域模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from okcode.matching import MatchExpression
from okcode.tools.models import JSONValue


class HookEvent(StrEnum):
    """OkCode 生命周期中可挂 Hook 的事件。"""

    SESSION_START = "session.start"
    SESSION_END = "session.end"
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    MESSAGE_USER = "message.user"
    MESSAGE_ASSISTANT = "message.assistant"
    TOOL_BEFORE = "tool.before"
    TOOL_AFTER = "tool.after"
    CONTEXT_COMPACTED = "system.context_compacted"
    ERROR = "system.error"


class ConditionMode(StrEnum):
    """同一条规则内多个条件的组合方式。"""

    ALL = "all"
    ANY = "any"


class PromptScope(StrEnum):
    """提示词注入的可见范围。"""

    NEXT_REQUEST = "next_request"
    TURN = "turn"
    SESSION = "session"


class HookActionType(StrEnum):
    """Hook 支持的动作类型。"""

    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"


@dataclass(frozen=True, slots=True)
class HookCondition:
    """一个字段匹配条件。"""

    field: str
    expression: MatchExpression


@dataclass(frozen=True, slots=True)
class HookConditionGroup:
    """一组同构逻辑组合条件。"""

    mode: ConditionMode
    conditions: tuple[HookCondition, ...]

    def summary(self) -> str:
        joiner = " 且 " if self.mode is ConditionMode.ALL else " 或 "
        return joiner.join(f"{item.field}={item.expression.to_text()}" for item in self.conditions)


@dataclass(frozen=True, slots=True)
class HookControl:
    """Hook 动作的执行控制。"""

    once: bool = False
    background: bool = False
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class ShellHookAction:
    """执行 shell 命令的 Hook 动作。"""

    command: str
    cwd: str | None = None
    intercept: bool = False
    deny_message: str | None = None

    @property
    def type(self) -> HookActionType:
        return HookActionType.SHELL


@dataclass(frozen=True, slots=True)
class PromptHookAction:
    """注入提示词的 Hook 动作。"""

    content: str
    scope: PromptScope = PromptScope.NEXT_REQUEST

    @property
    def type(self) -> HookActionType:
        return HookActionType.PROMPT


@dataclass(frozen=True, slots=True)
class HttpHookAction:
    """发起 HTTP 请求的 Hook 动作。"""

    url: str
    method: str = "POST"
    headers: Mapping[str, str] = field(default_factory=dict)
    body: JSONValue = None

    @property
    def type(self) -> HookActionType:
        return HookActionType.HTTP


@dataclass(frozen=True, slots=True)
class SubAgentHookAction:
    """子 Agent 占位动作。"""

    task: str
    profile: str | None = None

    @property
    def type(self) -> HookActionType:
        return HookActionType.SUBAGENT


type HookAction = ShellHookAction | PromptHookAction | HttpHookAction | SubAgentHookAction


@dataclass(frozen=True, slots=True)
class HookRule:
    """一条已校验的 Hook 规则。"""

    identifier: str
    event: HookEvent
    conditions: HookConditionGroup | None
    action: HookAction
    control: HookControl
    enabled: bool = True

    def condition_summary(self) -> str:
        return "无条件" if self.conditions is None else self.conditions.summary()


@dataclass(frozen=True, slots=True)
class HookContext:
    """一次 Hook 分发时可供条件和动作读取的脱敏上下文。"""

    event: HookEvent
    values: Mapping[str, JSONValue]

    def value(self, field_name: str) -> JSONValue:
        return self.values.get(field_name)


@dataclass(frozen=True, slots=True)
class HookInterception:
    """工具执行前 Hook 返回的拦截结果。"""

    reason: str
    rule_identifier: str


@dataclass(frozen=True, slots=True)
class HookRunRecord:
    """HookRuntime 中用于测试和日志的轻量记录。"""

    rule_identifier: str
    event: HookEvent
    status: str
    message: str = ""
