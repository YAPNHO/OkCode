"""可控的异步字节流辅助工具。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

import httpx


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = tuple(chunks)
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def sse_event(payload: str) -> bytes:
    return f"data: {payload}\n\n".encode()
