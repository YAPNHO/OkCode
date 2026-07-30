"""串行后台长期记忆 Worker。"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable

from okcode.memory.models import MemoryJob
from okcode.memory.request import MemoryRequestFactory
from okcode.memory.store import MemoryStore
from okcode.models import Role, StreamCompleted
from okcode.providers.base import LLMProvider

ProviderFactory = Callable[[], LLMProvider]
_STOP = object()


class MemoryWorker:
    """在线程独立事件循环中按提交顺序更新长期记忆。"""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        store: MemoryStore,
        request_factory: MemoryRequestFactory | None = None,
        *,
        join_timeout: float = 2.0,
    ) -> None:
        self._provider_factory = provider_factory
        self._store = store
        self._request_factory = request_factory or MemoryRequestFactory()
        self._join_timeout = join_timeout
        self._queue: queue.Queue[MemoryJob | object] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False

    def submit(self, job: MemoryJob) -> None:
        """非阻塞地提交任务；关闭后的任务安全丢弃。"""

        with self._lock:
            if self._closed:
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="okcode-memory", daemon=True)
                self._thread.start()
            self._queue.put(job)

    def close(self) -> None:
        """停止接收新任务，并有限等待后台线程收尾。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if thread is not None:
                self._queue.put(_STOP)
        if thread is not None:
            thread.join(timeout=self._join_timeout)

    def _run(self) -> None:
        asyncio.run(self._consume())

    async def _consume(self) -> None:
        provider: LLMProvider | None = None
        try:
            while True:
                item = await asyncio.to_thread(self._queue.get)
                if item is _STOP:
                    return
                assert isinstance(item, MemoryJob)
                try:
                    if provider is None:
                        provider = self._provider_factory()
                    await self._process(provider, item)
                except Exception:
                    continue
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception:
                    pass

    async def _process(self, provider: LLMProvider, job: MemoryJob) -> None:
        user_index, project_index = self._store.read_indexes()
        request = self._request_factory.build(job, user_index, project_index)
        completed: StreamCompleted | None = None
        async for event in provider.stream(request):
            if isinstance(event, StreamCompleted):
                if completed is not None:
                    raise ValueError("记忆模型流返回了多个完成事件。")
                completed = event
                continue
            if completed is not None:
                raise ValueError("记忆模型完成后出现额外事件。")
        if completed is None:
            raise ValueError("记忆模型流没有完成事件。")
        message = completed.message
        if message.role is not Role.ASSISTANT or message.tool_calls:
            raise ValueError("记忆模型响应不能包含工具调用。")
        self._store.apply(self._request_factory.parse(message.content))
