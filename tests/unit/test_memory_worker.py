from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.memory.models import MemoryJob, MemoryPaths
from okcode.memory.store import MemoryStore
from okcode.memory.worker import MemoryWorker
from okcode.models import ChatMessage, Role, StreamCompleted
from tests.fakes import FakeProvider


def _now() -> datetime:
    return datetime(2026, 7, 30, 10, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(
        MemoryPaths(tmp_path / "project-memory", tmp_path / "user-memory"), clock=_now
    )


def _response(note_ref: str) -> str:
    return json.dumps(
        {
            "operations": [
                {
                    "scope": "user",
                    "category": "preference",
                    "action": "create",
                    "note_ref": note_ref,
                    "title": "偏好",
                    "content": note_ref,
                }
            ],
            "user_index": [
                {
                    "note_ref": note_ref,
                    "category": "preference",
                    "summary": note_ref,
                }
            ],
            "project_index": [],
        },
        ensure_ascii=False,
    )


def _job(text: str) -> MemoryJob:
    return MemoryJob((ChatMessage(Role.USER, text), ChatMessage(Role.ASSISTANT, "完成")))


def test_worker_processes_jobs_serially_and_closes_its_provider(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [StreamCompleted(ChatMessage(Role.ASSISTANT, _response("one")))],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, _response("two")))],
        ]
    )
    factory_calls = 0

    def provider_factory() -> FakeProvider:
        nonlocal factory_calls
        factory_calls += 1
        return provider

    store = _store(tmp_path)
    worker = MemoryWorker(provider_factory, store)
    worker.submit(_job("第一轮"))
    worker.submit(_job("第二轮"))
    worker.close()

    assert factory_calls == 1
    assert provider.closed is True
    assert (tmp_path / "user-memory/one.md").is_file()
    assert (tmp_path / "user-memory/two.md").is_file()
    assert [
        request.messages[0].content.count("[本轮消息]") for request in provider.provider_requests
    ] == [1, 1]


def test_worker_isolates_failed_job_and_continues_next_job(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [ProviderError(ProviderErrorKind.STREAM, "失败")],
            [StreamCompleted(ChatMessage(Role.ASSISTANT, _response("after-failure")))],
        ]
    )
    worker = MemoryWorker(lambda: provider, _store(tmp_path))
    worker.submit(_job("会失败"))
    worker.submit(_job("会成功"))
    worker.close()

    assert provider.closed is True
    assert (tmp_path / "user-memory/after-failure.md").is_file()


def test_worker_drops_submissions_after_close(tmp_path: Path) -> None:
    provider = FakeProvider([])
    worker = MemoryWorker(lambda: provider, _store(tmp_path))
    worker.close()
    worker.submit(_job("忽略"))

    assert provider.provider_requests == []
