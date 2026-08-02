"""受管理 worktree 名称校验。"""

from __future__ import annotations

import re

from okcode.errors import ConfigError

_ALLOWED = re.compile(r"^[A-Za-z0-9._\-/]+$")
_MAX_TOTAL_LENGTH = 160
_MAX_SEGMENT_LENGTH = 64


def validate_worktree_name(raw: str) -> str:
    """返回规范化后的安全 worktree 名称。"""

    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("worktree 名称不能为空。")
    name = raw.strip().replace("\\", "\uffff")
    if ":" in name or name.startswith("/") or name.startswith("\uffff"):
        raise ConfigError(f"worktree 名称不能是绝对路径：{raw}")
    if "\\" in raw:
        raise ConfigError(f"worktree 名称不能包含反斜杠：{raw}")
    if len(name) > _MAX_TOTAL_LENGTH:
        raise ConfigError(f"worktree 名称过长，最多 {_MAX_TOTAL_LENGTH} 个字符：{raw}")
    if not _ALLOWED.match(name):
        raise ConfigError(f"worktree 名称包含非法字符：{raw}")
    parts = name.split("/")
    for part in parts:
        if not part:
            raise ConfigError(f"worktree 名称不能包含空路径段：{raw}")
        if part in {".", ".."}:
            raise ConfigError(f"worktree 名称不能包含 . 或 .. 路径段：{raw}")
        if len(part) > _MAX_SEGMENT_LENGTH:
            raise ConfigError(f"worktree 名称单段过长，最多 {_MAX_SEGMENT_LENGTH} 个字符：{raw}")
        if any(ord(ch) < 32 for ch in part):
            raise ConfigError(f"worktree 名称不能包含控制字符：{raw}")
    return "/".join(parts)


def validate_branch_component(raw: str) -> str:
    """校验可放进分支名的组件。"""

    value = validate_worktree_name(raw)
    if value.endswith(".") or value.endswith(".lock") or "@{" in value:
        raise ConfigError(f"worktree 分支名组件非法：{raw}")
    return value


def derive_agent_worktree_name(role_name: str | None, task_id: str) -> str:
    """按角色和任务号派生默认 worktree 名称。"""

    role = _slug(role_name or "fork")
    short_task = _slug(task_id)[:12] or "task"
    return validate_worktree_name(f"agents/{role}/{short_task}")


def derive_agent_branch_name(name: str) -> str:
    """按 worktree 名称派生独立分支名。"""

    safe_name = validate_branch_component(name)
    return f"okcode/agents/{safe_name}"


def _slug(raw: str) -> str:
    lowered = raw.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered)
    slug = slug.strip(".-_/")
    return slug or "agent"
