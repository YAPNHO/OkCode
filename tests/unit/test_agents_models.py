from __future__ import annotations

from okcode.agents.models import (
    AgentLaunchKind,
    AgentModelKind,
    AgentModelPolicy,
    AgentRole,
    AgentRoleCatalog,
    AgentRoleSourceKind,
    AgentTaskStatus,
    AgentUsage,
)
from okcode.models import TokenUsage
from okcode.prompt.cache import PromptCacheUsage


def test_agent_model_defaults_and_catalog_listing(tmp_path) -> None:
    role = AgentRole(
        name="reviewer",
        description="审查",
        source_kind=AgentRoleSourceKind.PROJECT,
        source_path=tmp_path / "reviewer.md",
        system_prompt="请审查。",
    )
    catalog = AgentRoleCatalog({"reviewer": role})

    assert AgentLaunchKind.DEFINED.value == "defined"
    assert AgentTaskStatus.COMPLETED.value == "completed"
    assert role.model_policy == AgentModelPolicy(AgentModelKind.INHERIT)
    assert catalog.get("reviewer") is role
    assert catalog.list_entries()[0].name == "reviewer"


def test_agent_usage_accumulates_tokens_and_tools() -> None:
    usage = AgentUsage()
    usage = usage.add_token_usage(
        TokenUsage(
            input_tokens=10,
            output_tokens=3,
            total_tokens=13,
            cache=PromptCacheUsage(read_tokens=5, write_tokens=7, available=True),
        )
    ).add_tool_calls(2)

    assert usage.input_tokens == 10
    assert usage.output_tokens == 3
    assert usage.total_tokens == 13
    assert usage.cache_read_tokens == 5
    assert usage.cache_write_tokens == 7
    assert usage.model_request_count == 1
    assert usage.tool_call_count == 2
