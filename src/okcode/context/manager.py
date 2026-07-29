"""轻量外置、预算估算、摘要计划和熔断状态。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from okcode.context.artifacts import ArtifactStore
from okcode.context.models import (
    ContextConfig,
    ConversationContextState,
    SummaryPlan,
    TokenEstimateAnchor,
)
from okcode.models import ChatMessage, ProviderRequest, Role, TokenUsage
from okcode.prompt import SystemInstruction
from okcode.tools.models import ToolExecutionResult

_SUMMARY_BOUNDARY = (
    "以上结构化摘要仅用于定位先前工作。需要文件、代码或工具结果的具体细节时，"
    "必须重新读取相关文件或重新执行工具；不得依据摘要臆测细节。"
)


class ContextManager:
    """管理单个会话的上下文压缩状态。"""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        config: ContextConfig | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self.config = config or ContextConfig()
        self.state = ConversationContextState()

    @property
    def circuit_open(self) -> bool:
        """摘要失败次数达到阈值后，不再发起内部摘要请求。"""

        return self.state.circuit_open

    def record_user_message(self, content: str) -> None:
        """按进入 Agent Loop 的顺序保留用户原文。"""

        self.state.original_user_messages = (*self.state.original_user_messages, content)

    def normalize_tool_results(
        self,
        results: Sequence[ToolExecutionResult],
    ) -> tuple[ToolExecutionResult, ...]:
        """按 50K 单项和 200K 单消息限制外置工具结果。"""

        original = tuple(results)
        sizes = tuple(len(result.to_json()) for result in original)
        selected = {
            index
            for index, (result, size) in enumerate(zip(original, sizes, strict=True))
            if size > self.config.max_tool_result_chars and not _is_artifact_preview(result)
        }

        remaining = sum(size for index, size in enumerate(sizes) if index not in selected)
        for index in _largest_indexes(sizes):
            if remaining <= self.config.max_tool_message_chars:
                break
            if index in selected or _is_artifact_preview(original[index]):
                continue
            selected.add(index)
            remaining -= sizes[index]

        normalized = list(original)
        for index in sorted(selected):
            artifact = self._artifact_store.externalize(original[index], index)
            normalized[index] = self._artifact_store.preview_result(original[index], artifact)

        while _total_result_chars(normalized) > self.config.max_tool_message_chars:
            next_index = next(
                (
                    index
                    for index in _largest_indexes(sizes)
                    if index not in selected and not _is_artifact_preview(original[index])
                ),
                None,
            )
            if next_index is None:
                raise ValueError("工具结果预览仍超过单条消息上下文上限。")
            artifact = self._artifact_store.externalize(original[next_index], next_index)
            normalized[next_index] = self._artifact_store.preview_result(
                original[next_index], artifact
            )
            selected.add(next_index)
        return tuple(normalized)

    def estimate_input(self, request: ProviderRequest) -> int:
        """以最近 Usage 锚点和完整请求字符变化量近似估算输入 Token。"""

        input_chars = request_character_count(request)
        anchor = self.state.estimate_anchor
        if anchor is None:
            return _chars_to_tokens(input_chars, self.config)
        return max(
            0,
            anchor.input_tokens + _chars_to_tokens(input_chars - anchor.input_chars, self.config),
        )

    def needs_automatic_compaction(self, request: ProviderRequest) -> bool:
        """仅在正常请求估算超过自动阈值时触发重量摘要。"""

        return self.estimate_input(request) > self.config.automatic_compaction_tokens

    def record_normal_usage(self, request: ProviderRequest, usage: TokenUsage) -> None:
        """记录最近一次正常请求的真实输入 Token，用于下一次估算。"""

        if usage.input_tokens is None:
            return
        self.state.estimate_anchor = TokenEstimateAnchor(
            input_tokens=usage.input_tokens,
            input_chars=request_character_count(request),
        )

    def plan_compaction(
        self,
        committed: Sequence[ChatMessage],
        pending: Sequence[ChatMessage],
        *,
        force: bool = False,
    ) -> SummaryPlan | None:
        """选择可安全摘要的已完成前缀；手动压缩可总结短会话。"""

        if self.state.circuit_open or not committed:
            return None
        messages = tuple(committed)
        retain_start = _retained_start(messages, self.config)
        if retain_start == 0:
            if not force:
                return None
            history_to_summarize = messages
            retained_history = messages
        else:
            history_to_summarize = messages[:retain_start]
            retained_history = messages[retain_start:]
        transcript = _build_transcript(self.state.summary, history_to_summarize, pending)
        return SummaryPlan(
            history_to_summarize=history_to_summarize,
            retained_history=retained_history,
            transcript=transcript,
            original_user_messages=self.state.original_user_messages,
        )

    def commit_summary(self, plan: SummaryPlan, summary: str) -> tuple[ChatMessage, ...]:
        """在摘要校验通过后，一次性更新动态上下文状态。"""

        self.state.summary = summary
        self.state.boundary_message = _SUMMARY_BOUNDARY
        self.reset_summary_failures()
        return plan.retained_history

    def record_summary_failure(self) -> bool:
        """记录一次失败，并返回本次是否刚打开熔断器。"""

        self.state.consecutive_summary_failures += 1
        if self.state.consecutive_summary_failures >= self.config.summary_failure_limit:
            self.state.circuit_open = True
            return True
        return False

    def reset_summary_failures(self) -> None:
        """成功摘要会清空连续失败状态。"""

        self.state.consecutive_summary_failures = 0
        self.state.circuit_open = False

    def system_instructions(self) -> tuple[SystemInstruction, ...]:
        """将已提交摘要作为动态系统补充注入正常请求。"""

        if self.state.summary is None or self.state.boundary_message is None:
            return ()
        return (
            SystemInstruction("context_summary", self.state.summary, 90),
            SystemInstruction("context_boundary", self.state.boundary_message, 91),
        )


def request_character_count(request: ProviderRequest) -> int:
    """覆盖正常请求所有会进入 Provider 输入的文本字段。"""

    parts = [request.prompt.stable_system]
    parts.extend(instruction.render() for instruction in request.prompt.dynamic_system)
    for message in request.messages:
        parts.append(message.role.value)
        parts.append(message.content)
        for call in message.tool_calls:
            parts.extend((call.id, call.name, call.arguments_json))
        for result in message.tool_results:
            parts.extend((result.tool_call_id, result.to_json()))
    for tool in request.tools:
        parts.extend(
            (
                tool.name,
                tool.description,
                json.dumps(
                    tool.input_schema,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    return sum(len(part) for part in parts)


def _build_transcript(
    old_summary: str | None,
    history_to_summarize: Sequence[ChatMessage],
    pending: Sequence[ChatMessage],
) -> str:
    sections: list[str] = []
    if old_summary:
        sections.append("[已有正式摘要]\n" + old_summary)
    if history_to_summarize:
        sections.append("[待摘要的已完成历史]\n" + _transcribe_messages(history_to_summarize))
    if pending:
        sections.append("[当前未提交消息，仅用于理解当前工作]\n" + _transcribe_messages(pending))
    return "\n\n".join(sections)


def _transcribe_messages(messages: Sequence[ChatMessage]) -> str:
    rows: list[str] = []
    for message in messages:
        rows.append(f"<{message.role.value}>")
        if message.content:
            rows.append(message.content)
        for call in message.tool_calls:
            rows.append(f"工具调用：id={call.id}，名称={call.name}，参数={call.arguments_json}")
        for result in message.tool_results:
            rows.append("工具结果：" + result.to_json())
        rows.append(f"</{message.role.value}>")
    return "\n".join(rows)


def _retained_start(messages: Sequence[ChatMessage], config: ContextConfig) -> int:
    index = len(messages)
    retained_messages = 0
    retained_tokens = 0
    while index > 0 and (
        retained_messages < config.retain_recent_messages
        or retained_tokens < config.retain_recent_tokens
    ):
        index -= 1
        retained_messages += 1
        retained_tokens += _message_tokens(messages[index], config)
    while (
        index > 0
        and messages[index].role is Role.TOOL
        and messages[index - 1].role is Role.ASSISTANT
        and messages[index - 1].tool_calls
    ):
        index -= 1
    return index


def _message_tokens(message: ChatMessage, config: ContextConfig) -> int:
    parts = [message.content]
    parts.extend(call.id + call.name + call.arguments_json for call in message.tool_calls)
    parts.extend(result.to_json() for result in message.tool_results)
    return _chars_to_tokens(sum(len(part) for part in parts), config)


def _chars_to_tokens(characters: int, config: ContextConfig) -> int:
    return characters // config.chars_per_token


def _largest_indexes(sizes: Sequence[int]) -> tuple[int, ...]:
    return tuple(sorted(range(len(sizes)), key=lambda index: (-sizes[index], index)))


def _total_result_chars(results: Sequence[ToolExecutionResult]) -> int:
    return sum(len(result.to_json()) for result in results)


def _is_artifact_preview(result: ToolExecutionResult) -> bool:
    artifact = result.data.get("context_artifact")
    return isinstance(artifact, Mapping)
