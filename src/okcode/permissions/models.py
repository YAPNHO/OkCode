"""权限规则与决策的领域模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from okcode.matching import MatchExpression, parse_match_expression
from okcode.models import ToolCall
from okcode.tools.models import JSONValue, PermissionTargetKind, ToolDefinition, ToolErrorCode


class PermissionMode(StrEnum):
    """未命中规则时的会话级权限兜底模式。"""

    STRICT = "strict"
    DEFAULT = "default"
    ALLOW = "allow"


class RuleAction(StrEnum):
    """规则允许的唯一结果。"""

    ALLOW = "allow"
    DENY = "deny"


class RuleSource(StrEnum):
    """权限决定的可观察来源。"""

    BLACKLIST = "blacklist"
    SANDBOX = "sandbox"
    SESSION = "session"
    PROJECT_LOCAL = "project_local"
    PROJECT = "project"
    USER = "user"
    MODE = "mode"
    USER_CONFIRMATION = "user_confirmation"


class PermissionRequestOrigin(StrEnum):
    """权限请求所属的运行域，用于隔离普通工具与内部自动化。"""

    TOOL = "tool"
    HOOK = "hook"


class PermissionConfirmation(StrEnum):
    """默认模式下用户可作出的确认选择。"""

    EXIT = "exit"
    DENY = "deny"
    ONCE = "once"
    SESSION = "session"
    PERMANENT = "permanent"


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """一个工具名及可选目标模式组成的权限规则。"""

    tool_name: str
    pattern: MatchExpression | str | None
    action: RuleAction

    def __post_init__(self) -> None:
        if isinstance(self.pattern, str):
            object.__setattr__(
                self,
                "pattern",
                parse_match_expression(self.pattern, "match 模式"),
            )

    def matches(self, request: PermissionRequest) -> bool:
        if self.tool_name != request.call.name:
            return False
        if self.pattern is None:
            return True
        if request.target is None:
            return False
        pattern = self.pattern
        assert isinstance(pattern, MatchExpression)
        target = request.target
        if request.target_kind is PermissionTargetKind.PATH:
            target = target.replace("\\", "/").casefold()
            expression = MatchExpression(
                pattern.kind,
                pattern.pattern.replace("\\", "/").casefold(),
                pattern.negated,
            )
        else:
            expression = pattern
        return expression.matches(target)

    def to_text(self) -> str:
        if self.pattern is None:
            return self.tool_name
        return f"{self.tool_name}({self.pattern.to_text()})"


@dataclass(frozen=True, slots=True)
class RuleSet:
    """一个固定优先级来源内、保持 YAML 顺序的规则集合。"""

    source: RuleSource
    rules: tuple[PermissionRule, ...]


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """已通过工具参数校验、可供权限层判断的一次调用。"""

    call: ToolCall
    tool: ToolDefinition
    arguments: Mapping[str, JSONValue]
    target_kind: PermissionTargetKind
    target: str | None
    display_target: str | None
    origin: PermissionRequestOrigin = PermissionRequestOrigin.TOOL


@dataclass(frozen=True, slots=True)
class SessionPermissionGrant:
    """只在当前权限管理器内存中生效的工具级会话授权。"""

    origin: PermissionRequestOrigin
    tool_name: str
    target_kind: PermissionTargetKind

    @classmethod
    def from_request(cls, request: PermissionRequest) -> SessionPermissionGrant:
        return cls(request.origin, request.call.name, request.target_kind)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """权限层的最终结论。"""

    allowed: bool
    source: RuleSource
    reason: str
    error_code: ToolErrorCode = ToolErrorCode.PERMISSION_DENIED


def parse_rule_text(text: object, known_tool_names: set[str]) -> tuple[str, MatchExpression | None]:
    """解析 ``工具名`` 或 ``工具名(模式)``，并兼容 Bash 命令工具别名。"""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("match 必须是非空字符串。")
    value = text.strip()
    if "(" not in value and ")" not in value:
        tool_name = value
        expression = None
    else:
        if value.count("(") != 1 or value.count(")") != 1 or not value.endswith(")"):
            raise ValueError("match 必须是 工具名 或 工具名(模式)。")
        tool_name, pattern_text = value.split("(", maxsplit=1)
        tool_name = tool_name.strip()
        pattern = pattern_text[:-1]
        if not tool_name or not pattern:
            raise ValueError("match 中的工具名和模式都不能为空。")
        if "(" in pattern or ")" in pattern:
            raise ValueError("match 模式不能包含括号。")
        try:
            expression = parse_match_expression(pattern, "match 模式")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    if tool_name == "Bash":
        tool_name = "run_command"
    if tool_name not in known_tool_names:
        raise ValueError(f"未知工具：{tool_name}")
    return tool_name, expression
