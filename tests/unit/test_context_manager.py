from __future__ import annotations

from pathlib import Path

from okcode.context import ArtifactStore, ContextConfig, ContextManager
from okcode.context.manager import request_character_count
from okcode.models import ChatMessage, ProviderRequest, Role, TokenUsage, ToolCall
from okcode.prompt import PromptBundle, PromptCachePolicy
from okcode.tools.models import ToolExecutionResult


def _result_with_json_size(size: int, index: int) -> ToolExecutionResult:
    empty = ToolExecutionResult(f"call-{index}", "read_file", True, "", None)
    result = ToolExecutionResult(
        f"call-{index}",
        "read_file",
        True,
        "x" * (size - len(empty.to_json())),
        None,
    )
    assert len(result.to_json()) == size
    return result


def _request(content: str) -> ProviderRequest:
    return ProviderRequest(
        messages=(ChatMessage(Role.USER, content),),
        tools=(),
        prompt=PromptBundle("system", (), "system", "key"),
        cache=PromptCachePolicy(),
    )


def test_light_compaction_externalizes_only_largest_required_result(tmp_path: Path) -> None:
    manager = ContextManager(ArtifactStore(tmp_path, "five-results"))
    results = tuple(
        _result_with_json_size(size, index)
        for index, size in enumerate((42_000, 38_000, 45_000, 40_000, 44_000))
    )

    normalized = manager.normalize_tool_results(results)

    assert normalized[:2] == results[:2]
    assert "context_artifact" in normalized[2].data
    assert normalized[3:] == results[3:]
    assert sum(len(result.to_json()) for result in normalized) <= 200_000


def test_estimate_uses_usage_anchor_then_falls_back_to_full_characters(tmp_path: Path) -> None:
    manager = ContextManager(ArtifactStore(tmp_path, "estimate"))
    first = _request("第一次请求")
    manager.record_normal_usage(first, TokenUsage(input_tokens=120))
    second = _request("第一次请求" + "x" * 40)

    expected = 120 + (request_character_count(second) - request_character_count(first)) // 4
    assert manager.estimate_input(second) == expected

    fallback = ContextManager(ArtifactStore(tmp_path, "fallback"))
    assert fallback.estimate_input(second) == request_character_count(second) // 4


def test_automatic_compaction_starts_only_above_167k(tmp_path: Path) -> None:
    manager = ContextManager(ArtifactStore(tmp_path, "threshold"))
    request = _request("x" * (167_001 * 4))

    assert manager.estimate_input(request) > 167_000
    assert manager.needs_automatic_compaction(request) is True


def test_summary_plan_keeps_complete_tool_turn_at_retained_boundary(tmp_path: Path) -> None:
    config = ContextConfig(retain_recent_tokens=1, retain_recent_messages=1)
    manager = ContextManager(ArtifactStore(tmp_path, "retain"), config)
    call = ToolCall("call-1", "read_file", "{}")
    result = ToolExecutionResult("call-1", "read_file", True, "完成", None)
    history = (
        ChatMessage(Role.USER, "较早请求"),
        ChatMessage(Role.ASSISTANT, tool_call=call),
        ChatMessage(Role.TOOL, tool_result=result),
    )

    plan = manager.plan_compaction(history, ())

    assert plan is not None
    assert plan.history_to_summarize == (history[0],)
    assert plan.retained_history == history[1:]


def test_success_resets_failures_and_third_failure_opens_circuit(tmp_path: Path) -> None:
    manager = ContextManager(ArtifactStore(tmp_path, "circuit"))
    history = (ChatMessage(Role.USER, "请求"),)
    plan = manager.plan_compaction(history, (), force=True)
    assert plan is not None

    assert manager.record_summary_failure() is False
    manager.commit_summary(plan, "正式摘要")
    assert manager.state.consecutive_summary_failures == 0
    assert manager.circuit_open is False
    assert manager.record_summary_failure() is False
    assert manager.record_summary_failure() is False
    assert manager.record_summary_failure() is True
    assert manager.circuit_open is True
    assert manager.plan_compaction(history, (), force=True) is None
