"""不可通过规则或确认放开的 Windows 高危命令。"""

from __future__ import annotations

import re

from okcode.permissions.models import PermissionDecision, PermissionRequest, RuleSource

_COMMAND_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:rmdir|rd|del|erase)\b(?=[^\r\n]*(?:/s|/q))(?=[^\r\n]*(?:[a-z]:\\|%systemdrive%|%windir%|%systemroot%))",
            re.IGNORECASE,
        ),
        "命令属于大范围删除操作。",
    ),
    (
        re.compile(
            r"\bremove-item\b(?=[^\r\n]*-(?:recurse|r)\b)(?=[^\r\n]*(?:[a-z]:\\|\$env:(?:systemdrive|windir|systemroot)))",
            re.IGNORECASE,
        ),
        "命令属于大范围删除操作。",
    ),
    (
        re.compile(r"\bformat(?:\.exe)?\s+[a-z]:", re.IGNORECASE),
        "命令属于磁盘格式化操作。",
    ),
    (
        re.compile(r"\bdiskpart(?:\.exe)?\b", re.IGNORECASE),
        "命令属于磁盘或分区破坏操作。",
    ),
    (
        re.compile(
            r"\b(?:clear-disk|remove-partition|remove-volume|format-volume)\b",
            re.IGNORECASE,
        ),
        "命令属于磁盘或分区破坏操作。",
    ),
    (
        re.compile(r"\bbcdedit\b(?=[^\r\n]*\/(?:delete|set)\b)", re.IGNORECASE),
        "命令可能破坏系统启动配置。",
    ),
    (
        re.compile(
            r"\bshutdown\b(?=[^\r\n]*\/(?:s|r)\b)|\b(?:restart|stop)-computer\b", re.IGNORECASE
        ),
        "命令属于关机或重启操作。",
    ),
)


def reject_blacklisted_command(request: PermissionRequest) -> PermissionDecision | None:
    """对命令工具检查不可绕过的高危操作。"""

    if request.call.name != "run_command" or request.target is None:
        return None
    for pattern, reason in _COMMAND_RULES:
        if pattern.search(request.target):
            return PermissionDecision(False, RuleSource.BLACKLIST, reason)
    return None
