"""后台 worktree 清理 Worker。"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from okcode.worktrees.manager import WorktreeManager
from okcode.worktrees.models import WorktreeCleanupPolicy


@dataclass(frozen=True, slots=True)
class WorktreeCleanupWorkerConfig:
    interval_seconds: float = 300.0


class WorktreeCleanupWorker:
    """定期触发过期 worktree 清理。"""

    def __init__(
        self,
        manager: WorktreeManager,
        *,
        policy: WorktreeCleanupPolicy | None = None,
        config: WorktreeCleanupWorkerConfig | None = None,
    ) -> None:
        self._manager = manager
        self._policy = policy or WorktreeCleanupPolicy()
        self._config = config or WorktreeCleanupWorkerConfig()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="okcode-worktree-cleanup",
            daemon=True,
        )
        self._thread.start()

    def close(self, timeout_seconds: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._config.interval_seconds):
            try:
                self._manager.cleanup_expired(self._policy)
            except Exception:
                # 清理失败不能影响主 Agent 生命周期。
                continue
