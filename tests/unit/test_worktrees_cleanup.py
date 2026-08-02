from okcode.worktrees.cleanup import WorktreeCleanupWorker, WorktreeCleanupWorkerConfig
from okcode.worktrees.models import WorktreeCleanupPolicy


class Manager:
    def __init__(self) -> None:
        self.calls = 0

    def cleanup_expired(self, policy: WorktreeCleanupPolicy):
        self.calls += 1
        return ()


def test_cleanup_worker_starts_and_closes() -> None:
    manager = Manager()
    worker = WorktreeCleanupWorker(
        manager, config=WorktreeCleanupWorkerConfig(interval_seconds=0.01)
    )

    worker.start()
    worker.close()

    assert manager.calls >= 0
