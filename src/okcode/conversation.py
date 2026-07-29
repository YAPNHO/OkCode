"""进程内多轮会话和原子提交。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from okcode.errors import ProviderError, ProviderErrorKind
from okcode.models import (
    ChatMessage,
    Role,
    StreamCompleted,
    ToolExecutionFinished,
    ToolExecutionStarted,
    TurnEvent,
)
from okcode.providers.base import LLMProvider
from okcode.tools.executor import ToolExecutor
from okcode.tools.registry import ToolRegistry


class ConversationSession:
    """保存当前运行期间的已完成对话。"""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._executor = executor
        self._messages: tuple[ChatMessage, ...] = ()

    @property
    def messages(self) -> tuple[ChatMessage, ...]:
        return self._messages

    async def stream_turn(self, user_text: str) -> AsyncIterator[TurnEvent]:
        """流式执行一轮，并只在完整成功后提交历史。"""

        user_message = ChatMessage(role=Role.USER, content=user_text)
        completed: ChatMessage | None = None
        snapshot = (*self._messages, user_message)

        async for event in self._provider.stream(snapshot, self._registry.definitions()):
            if isinstance(event, StreamCompleted):
                if completed is not None:
                    raise ProviderError(ProviderErrorKind.STREAM, "模型流返回了多个完成事件。")
                completed = event.message
                continue
            if completed is not None:
                raise ProviderError(ProviderErrorKind.STREAM, "完成事件之后出现了额外增量。")
            yield event

        if completed is None:
            raise ProviderError(ProviderErrorKind.STREAM, "模型流没有正常结束。")
        if completed.role is not Role.ASSISTANT:
            raise ProviderError(ProviderErrorKind.STREAM, "模型完成事件不是助手消息。")
        if completed.tool_call is not None:
            yield ToolExecutionStarted(completed.tool_call.name)
            result = await self._executor.execute(completed.tool_call)
            yield ToolExecutionFinished(result)
            tool_message = ChatMessage(role=Role.TOOL, tool_result=result)
            self._messages = (*self._messages, user_message, completed, tool_message)
            return
        if not completed.content.strip():
            raise ProviderError(ProviderErrorKind.STREAM, "模型未返回可显示的正式回答。")
        self._messages = (*self._messages, user_message, completed)
