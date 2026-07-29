from __future__ import annotations

import pytest

from okcode.models import ChatMessage, ProviderRequest, Role, TokenUsage, ToolCall
from okcode.prompt import PromptBundle, PromptCachePolicy, PromptCacheUsage
from okcode.tools.models import ToolDefinition, ToolErrorCode, ToolExecutionResult


def test_chat_messages_enforce_role_specific_content() -> None:
    user = ChatMessage(Role.USER, "问题")
    call = ToolCall("call-1", "read_file", '{"path":"a.txt"}')
    assistant = ChatMessage(Role.ASSISTANT, tool_call=call)
    result = ToolExecutionResult("call-1", "read_file", True, "成功", None)
    tool = ChatMessage(Role.TOOL, tool_result=result)

    assert user.content == "问题"
    assert assistant.tool_call == call
    assert tool.tool_result == result

    with pytest.raises(ValueError):
        ChatMessage(Role.USER, "", tool_call=call)
    with pytest.raises(ValueError):
        ChatMessage(Role.ASSISTANT)
    with pytest.raises(ValueError):
        ChatMessage(Role.TOOL, "不允许", tool_result=result)


def test_tool_result_serializes_stably_without_provider_state() -> None:
    result = ToolExecutionResult(
        tool_call_id="call-1",
        tool_name="edit_file",
        success=False,
        content="原文未找到。",
        error_code=ToolErrorCode.MATCH_NOT_FOUND,
        data={"match_count": 0},
    )
    encoded = result.to_json()

    assert '"error_code":"match_not_found"' in encoded
    assert '"success":false' in encoded
    assert "provider_state" not in repr(
        ChatMessage(Role.ASSISTANT, "答案", provider_state={"secret": "不显示"})
    )


def test_provider_request_and_cache_usage_keep_old_token_defaults() -> None:
    tool = ToolDefinition(
        name="read_file",
        description="读取文件",
        input_schema={"type": "object"},
        timeout_seconds=5,
    )
    request = ProviderRequest(
        messages=(ChatMessage(Role.USER, "问题"),),
        tools=(tool,),
        prompt=PromptBundle("稳定提示", (), "稳定提示", "key"),
        cache=PromptCachePolicy(enabled=True),
    )
    usage = TokenUsage(input_tokens=10, output_tokens=2)

    assert request.cache.enabled is True
    assert usage.cache == PromptCacheUsage.unavailable()
    assert TokenUsage.unavailable().cache.available is False
