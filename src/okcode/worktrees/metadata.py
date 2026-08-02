"""worktree 元数据读写。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from okcode.errors import ConfigError
from okcode.worktrees.models import (
    WorktreeIdentity,
    WorktreeInitializationReport,
    WorktreeMetadata,
)

METADATA_VERSION = 1
METADATA_RELATIVE_PATH = Path(".okcode") / "worktree.json"


def metadata_path(worktree_path: Path) -> Path:
    return worktree_path / METADATA_RELATIVE_PATH


def write_metadata(metadata: WorktreeMetadata) -> None:
    path = metadata_path(metadata.worktree_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_to_json(metadata), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_metadata(worktree_path: Path) -> WorktreeMetadata:
    path = metadata_path(worktree_path)
    if not path.exists():
        raise ConfigError(f"worktree 元数据不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取 worktree 元数据：{path}") from exc
    try:
        return _from_json(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"worktree 元数据格式无效：{path}") from exc


def validate_metadata(
    metadata: WorktreeMetadata,
    *,
    repo_root: Path,
    repo_common_dir: Path,
    managed_root: Path,
    worktree_path: Path,
    identity: WorktreeIdentity,
) -> None:
    if metadata.version != METADATA_VERSION:
        raise ConfigError("worktree 元数据版本不受支持。")
    checks = (
        (metadata.repo_root, repo_root, "repo_root"),
        (metadata.repo_common_dir, repo_common_dir, "repo_common_dir"),
        (metadata.managed_root, managed_root, "managed_root"),
        (metadata.worktree_path, worktree_path, "worktree_path"),
    )
    for actual, expected, field in checks:
        if actual.resolve() != expected.resolve():
            raise ConfigError(f"worktree 元数据字段 {field} 不匹配。")
    if metadata.identity != identity:
        raise ConfigError("worktree 元数据身份不匹配。")


def _to_json(metadata: WorktreeMetadata) -> dict[str, Any]:
    data = asdict(metadata)
    for key in ("repo_root", "repo_common_dir", "managed_root", "worktree_path"):
        data[key] = str(getattr(metadata, key))
    for key in ("created_at", "last_used_at", "expires_at"):
        value = getattr(metadata, key)
        data[key] = value.isoformat() if value is not None else None
    return data


def _from_json(data: dict[str, Any]) -> WorktreeMetadata:
    identity_data = data["identity"]
    initialization_data = data.get("initialization") or {}
    version = int(data["version"])
    if version != METADATA_VERSION:
        raise ConfigError("worktree 元数据版本不受支持。")
    return WorktreeMetadata(
        version=version,
        repo_root=Path(data["repo_root"]),
        repo_common_dir=Path(data["repo_common_dir"]),
        managed_root=Path(data["managed_root"]),
        worktree_path=Path(data["worktree_path"]),
        identity=WorktreeIdentity(**identity_data),
        base_ref=str(data["base_ref"]),
        base_head=str(data["base_head"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        last_used_at=datetime.fromisoformat(data["last_used_at"]),
        expires_at=(
            datetime.fromisoformat(data["expires_at"])
            if data.get("expires_at") is not None
            else None
        ),
        initialization=WorktreeInitializationReport(
            copied_files=tuple(initialization_data.get("copied_files", ())),
            linked_directories=tuple(initialization_data.get("linked_directories", ())),
            hook_mode=str(initialization_data.get("hook_mode", "skipped")),
            warnings=tuple(initialization_data.get("warnings", ())),
        ),
    )
