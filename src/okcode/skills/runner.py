"""独立模式 Skill 的临时对话执行。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from okcode.models import ChatMessage, ProviderRequest, Role, StreamCompleted
from okcode.prompt import (
    PromptBuilder,
    PromptCachePolicy,
    SystemInstruction,
    enhance_tool_definitions,
)
from okcode.prompt.builder import PromptBuildContext, PromptOptionalSections
from okcode.prompt.modes import TurnKind
from okcode.providers.base import LLMProvider
from okcode.skills.models import SkillActivation, SkillHistoryMode
from okcode.tools.executor import ToolExecutor
from okcode.tools.models import ToolDefinition


@dataclass(frozen=True, slots=True)
class SkillRunResult:
    """独立模式执行结果。"""

    success: bool
    summary: str
    error_message: str | None = None


class SkillRunner:
    """运行隔离临时对话。"""

    def __init__(
        self,
        provider: LLMProvider,
        prompt_builder: PromptBuilder | None = None,
        cache_policy: PromptCachePolicy | None = None,
        executor: ToolExecutor | None = None,
        *,
        max_iterations: int = 12,
    ) -> None:
        self._provider = provider
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._cache_policy = cache_policy or PromptCachePolicy()
        self._executor = executor
        self._max_iterations = max_iterations

    def select_history(
        self,
        messages: Sequence[ChatMessage],
        history_mode: SkillHistoryMode,
    ) -> tuple[ChatMessage, ...]:
        if history_mode is SkillHistoryMode.NONE:
            return ()
        if history_mode is SkillHistoryMode.RECENT:
            return _recent_complete_history(messages, 8)
        if history_mode is SkillHistoryMode.SUMMARY:
            return ()
        return tuple(messages)

    async def run(
        self,
        activation: SkillActivation,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        history_mode: SkillHistoryMode | None = None,
        history_summary: str | None = None,
    ) -> SkillRunResult:
        selected = self.select_history(messages, history_mode or activation.history_mode)
        visible_tools = enhance_tool_definitions(tools)
        additional = ()
        if (
            history_summary
            and (history_mode or activation.history_mode) is SkillHistoryMode.SUMMARY
        ):
            additional = (SystemInstruction("context_summary", history_summary, priority=90),)
        prompt_context = PromptBuildContext(
            workspace_root="",
            platform="",
            current_date="",
            available_tool_names=tuple(tool.name for tool in visible_tools),
            turn_kind=TurnKind.NORMAL,
            optional_sections=PromptOptionalSections(active_skills=activation.rendered_sop),
            additional_system_instructions=additional,
        )
        prompt = self._prompt_builder.build(prompt_context, visible_tools)
        history = [*selected, ChatMessage(Role.USER, "请按已激活 Skill 执行任务，并返回摘要。")]
        try:
            for iteration in range(self._max_iterations + 1):
                request = ProviderRequest(
                    messages=tuple(history),
                    tools=visible_tools,
                    prompt=prompt,
                    cache=self._cache_policy,
                    model_override=activation.model,
                )
                completed: StreamCompleted | None = None
                async for event in self._provider.stream(request):
                    if isinstance(event, StreamCompleted):
                        if completed is not None:
                            return SkillRunResult(False, "", "独立 Skill 收到了多个完成事件。")
                        completed = event
                if completed is None:
                    return SkillRunResult(False, "", "独立 Skill 的模型流没有正常结束。")
                message = completed.message
                if message.role is not Role.ASSISTANT:
                    return SkillRunResult(False, "", "独立 Skill 收到无效的模型角色。")
                history.append(message)
                if not message.tool_calls:
                    if not message.content.strip():
                        return SkillRunResult(False, "", "独立 Skill 未返回可用摘要。")
                    return SkillRunResult(True, message.content)
                if self._executor is None:
                    return SkillRunResult(False, "", "独立 Skill 未配置工具执行器。")
                if iteration >= self._max_iterations:
                    return SkillRunResult(
                        False,
                        "",
                        f"已达到 {self._max_iterations} 次独立工具迭代上限。",
                    )
                results = [await self._executor.execute(call) for call in message.tool_calls]
                history.append(ChatMessage(Role.TOOL, tool_results=tuple(results)))
            return SkillRunResult(False, "", f"已达到 {self._max_iterations} 次独立工具迭代上限。")
        except Exception as exc:
            return SkillRunResult(False, "", f"{type(exc).__name__}: {exc}")


def _recent_complete_history(
    messages: Sequence[ChatMessage], limit: int
) -> tuple[ChatMessage, ...]:
    """截取最近历史时不拆开助手工具调用和对应的工具结果。"""

    start = max(0, len(messages) - limit)
    while start > 0 and messages[start].role is Role.TOOL:
        start -= 1
    return tuple(messages[start:])
