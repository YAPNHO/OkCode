"""Windows 友好的锁文件机制。"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class LockAcquireError(RuntimeError):
    """拿不到锁时抛出的可诊断错误。"""


@dataclass(frozen=True, slots=True)
class FileLockLease:
    """当前进程持有的一把文件锁。"""

    lock_path: Path
    token: str
    owner: str
    stale_takeover: bool = False


class FileLock:
    """通过独占创建锁文件实现的跨进程锁。"""

    @staticmethod
    def acquire(
        lock_path: Path,
        *,
        timeout_seconds: float = 5.0,
        stale_seconds: float = 30.0,
        owner: str = "okcode",
    ) -> FileLockLease:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        deadline = time.monotonic() + timeout_seconds
        stale_takeover = False
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if _is_stale(lock_path, stale_seconds):
                    try:
                        lock_path.unlink()
                        stale_takeover = True
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise LockAcquireError(f"获取锁超时：{lock_path}")
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "owner": owner,
                        "token": token,
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            return FileLockLease(lock_path, token, owner, stale_takeover)

    @staticmethod
    def release(lease: FileLockLease) -> None:
        try:
            raw = json.loads(lease.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if raw.get("token") == lease.token:
            try:
                lease.lock_path.unlink()
            except OSError:
                return


def _is_stale(lock_path: Path, stale_seconds: float) -> bool:
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False
    return age >= stale_seconds
