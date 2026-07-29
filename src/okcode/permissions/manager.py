"""五层权限决策及会话级权限状态。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from okcode.models import ToolCall
from okcode.permissions.blacklist import reject_blacklisted_command
from okcode.permissions.models import (
    PermissionConfirmation,
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
    PermissionRule,
    RuleAction,
    RuleSet,
    RuleSource,
)
from okcode.permissions.rules import PermissionPaths, append_local_allow_rule
from okcode.tools.models import (
    JSONValue,
    PermissionTargetKind,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
)
from okcode.tools.workspace import Workspace

PermissionConfirmer = Callable[[PermissionRequest], PermissionConfirmation]


class PermissionManager:
    """按固定顺序决定某次已校验工具调用能否实际执行。"""

    def __init__(
        self,
        workspace: Workspace,
        rule_sets: tuple[RuleSet, ...],
        paths: PermissionPaths,
        known_tool_names: set[str],
        *,
        mode: PermissionMode = PermissionMode.DEFAULT,
        confirmer: PermissionConfirmer | None = None,
    ) -> None:
        self._workspace = workspace
        self._rule_sets = rule_sets
        self._paths = paths
        self._known_tool_names = known_tool_names
        self._mode = mode
        self._confirmer = confirmer or _deny_confirmation
        self._session_rules: list[PermissionRule] = []

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    @property
    def paths(self) -> PermissionPaths:
        return self._paths

    def set_mode(self, mode: PermissionMode | str) -> None:
        self._mode = PermissionMode(mode)

    def allow_for_session(self, request: PermissionRequest) -> None:
        """为当前进程加入一条精确的会话允许规则。"""

        self._session_rules.append(_allow_rule_for(request))

    def authorize(
        self,
        call: ToolCall,
        tool: ToolDefinition,
        arguments: Mapping[str, JSONValue],
    ) -> PermissionDecision:
        """返回终局权限结论；拒绝路径不会执行工具业务代码。"""

        request_or_decision = self._build_request(call, tool, arguments)
        if isinstance(request_or_decision, PermissionDecision):
            return request_or_decision
        request = request_or_decision

        blacklisted = reject_blacklisted_command(request)
        if blacklisted is not None:
            return blacklisted

        matched = self._resolve_rule(request)
        if matched is not None:
            action, source = matched
            if action is RuleAction.ALLOW:
                return PermissionDecision(True, source, "调用已被权限规则允许。")
            return PermissionDecision(False, source, "调用被权限规则拒绝。")

        if self._mode is PermissionMode.STRICT:
            return PermissionDecision(False, RuleSource.MODE, "严格模式拒绝未匹配规则的调用。")
        if self._mode is PermissionMode.ALLOW:
            return PermissionDecision(True, RuleSource.MODE, "放行模式允许未匹配规则的调用。")

        return self._confirm(request)

    def _build_request(
        self,
        call: ToolCall,
        tool: ToolDefinition,
        arguments: Mapping[str, JSONValue],
    ) -> PermissionRequest | PermissionDecision:
        target_definition = tool.permission_target
        if target_definition.kind is PermissionTargetKind.NONE:
            return PermissionRequest(call, tool, arguments, PermissionTargetKind.NONE, None, None)
        argument_name = target_definition.argument_name
        if argument_name is None:
            return PermissionDecision(False, RuleSource.SANDBOX, "工具缺少权限目标配置。")

        raw_value = arguments.get(argument_name)
        if target_definition.optional and raw_value is None:
            raw_value = "."
        if not isinstance(raw_value, str):
            return PermissionDecision(False, RuleSource.SANDBOX, "工具权限目标无效。")
        if target_definition.kind is PermissionTargetKind.COMMAND:
            return PermissionRequest(
                call, tool, arguments, PermissionTargetKind.COMMAND, raw_value, raw_value
            )
        try:
            _, relative = self._workspace.resolve_path_with_relative(raw_value, must_exist=False)
        except ToolFailure as failure:
            if failure.code is ToolErrorCode.OUTSIDE_WORKSPACE:
                return PermissionDecision(
                    False,
                    RuleSource.SANDBOX,
                    "路径必须位于当前项目目录内。",
                    ToolErrorCode.OUTSIDE_WORKSPACE,
                )
            return PermissionDecision(False, RuleSource.SANDBOX, "无法安全解析工具路径。")
        return PermissionRequest(
            call, tool, arguments, PermissionTargetKind.PATH, relative, relative
        )

    def _resolve_rule(self, request: PermissionRequest) -> tuple[RuleAction, RuleSource] | None:
        for rule in self._session_rules:
            if rule.matches(request):
                return rule.action, RuleSource.SESSION
        for rule_set in self._rule_sets:
            for rule in rule_set.rules:
                if rule.matches(request):
                    return rule.action, rule_set.source
        return None

    def _confirm(self, request: PermissionRequest) -> PermissionDecision:
        try:
            confirmation = self._confirmer(request)
        except (EOFError, KeyboardInterrupt):
            confirmation = PermissionConfirmation.DENY
        except Exception:
            confirmation = PermissionConfirmation.DENY
        if confirmation is PermissionConfirmation.ONCE:
            return PermissionDecision(True, RuleSource.USER_CONFIRMATION, "用户允许本次调用。")
        if confirmation is PermissionConfirmation.SESSION:
            self.allow_for_session(request)
            return PermissionDecision(True, RuleSource.USER_CONFIRMATION, "用户允许本会话调用。")
        if confirmation is PermissionConfirmation.PERMANENT:
            rule = _allow_rule_for(request)
            try:
                append_local_allow_rule(self._paths, rule, self._known_tool_names)
            except Exception:
                return PermissionDecision(
                    False, RuleSource.USER_CONFIRMATION, "无法保存永久允许规则，调用未执行。"
                )
            self._replace_local_rules(rule)
            return PermissionDecision(True, RuleSource.USER_CONFIRMATION, "用户永久允许此调用。")
        return PermissionDecision(False, RuleSource.USER_CONFIRMATION, "用户拒绝了此调用。")

    def _replace_local_rules(self, rule: PermissionRule) -> None:
        updated: list[RuleSet] = []
        found_local = False
        for rule_set in self._rule_sets:
            if rule_set.source is RuleSource.PROJECT_LOCAL:
                updated.append(RuleSet(rule_set.source, (*rule_set.rules, rule)))
                found_local = True
            else:
                updated.append(rule_set)
        if not found_local:
            updated.insert(0, RuleSet(RuleSource.PROJECT_LOCAL, (rule,)))
        self._rule_sets = tuple(updated)


def _allow_rule_for(request: PermissionRequest) -> PermissionRule:
    return PermissionRule(request.call.name, request.target, RuleAction.ALLOW)


def _deny_confirmation(_: PermissionRequest) -> PermissionConfirmation:
    return PermissionConfirmation.DENY
