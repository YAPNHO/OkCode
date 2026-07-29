"""Provider 抽象接口。"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from okcode.models import ChatMessage, StreamEvent
from okcode.tools.models import ToolDefinition


class LLMProvider(Protocol):
    """统一流式对话 Provider。"""

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]:
        """流式生成一轮回复。"""

    async def aclose(self) -> None:
        """释放客户端资源，可重复调用。"""
