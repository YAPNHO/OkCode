from __future__ import annotations

import pytest

from okcode.context import SummaryPlan, SummaryRequestFactory
from okcode.context.summary import SUMMARY_HEADINGS
from okcode.models import ChatMessage, Role


def _plan() -> SummaryPlan:
    return SummaryPlan(
        history_to_summarize=(ChatMessage(Role.USER, "原始请求"),),
        retained_history=(),
        transcript="<user>\n原始请求\n</user>",
        original_user_messages=("第一条用户原文", "第二条用户原文"),
    )


def _formal_response(*, placeholder: str = "{{ALL_USER_MESSAGES}}") -> str:
    sections = []
    for heading in SUMMARY_HEADINGS:
        content = placeholder if heading == "所有用户消息" else f"{heading}内容"
        sections.append(f"## {heading}\n{content}")
    return (
        "<analysis_draft>只在这里出现的草稿</analysis_draft>\n<formal_summary>\n"
        + "\n".join(sections)
        + "\n</formal_summary>"
    )


def test_summary_request_has_no_tools_or_cache_and_forbids_tool_calls() -> None:
    request = SummaryRequestFactory().build(_plan())

    assert request.tools == ()
    assert request.cache.enabled is False
    assert "绝对禁止调用工具" in request.prompt.stable_system
    assert "<analysis_draft>" in request.prompt.stable_system
    assert "<formal_summary>" in request.prompt.stable_system


def test_extract_discards_draft_and_injects_verbatim_user_messages() -> None:
    summary = SummaryRequestFactory().extract_final_summary(_formal_response(), _plan())

    assert "只在这里出现的草稿" not in summary
    assert "第一条用户原文\n\n第二条用户原文" in summary
    assert "{{ALL_USER_MESSAGES}}" not in summary
    assert all(f"## {heading}" in summary for heading in SUMMARY_HEADINGS)


@pytest.mark.parametrize(
    "response",
    (
        "",
        "<formal_summary>不完整</formal_summary>",
        _formal_response(placeholder="用户消息由模型转述"),
        _formal_response().replace("## 当前工作", "## 最近工作"),
    ),
)
def test_extract_rejects_invalid_summary_contract(response: str) -> None:
    with pytest.raises(ValueError):
        SummaryRequestFactory().extract_final_summary(response, _plan())
