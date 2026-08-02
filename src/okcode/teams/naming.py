"""团队名和成员名安全校验。"""

from __future__ import annotations

import re

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_team_name(name: str) -> str:
    """校验并返回安全的小组名。"""

    return _validate_name(name, "团队名")


def validate_member_name(name: str) -> str:
    """校验并返回安全的成员名。"""

    return _validate_name(name, "成员名")


def _validate_name(name: str, label: str) -> str:
    value = name.strip() if isinstance(name, str) else ""
    if not value:
        raise ValueError(f"{label}不能为空。")
    if value in {".", ".."}:
        raise ValueError(f"{label}不能是单点或双点。")
    if len(value) > 80:
        raise ValueError(f"{label}不能超过 80 个字符。")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{label}不能包含控制字符。")
    if "/" in value or "\\" in value or ":" in value:
        raise ValueError(f"{label}不能包含路径分隔符或盘符冒号。")
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label}只能包含字母、数字、短横线、下划线和点号。")
    return value
