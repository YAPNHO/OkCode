from __future__ import annotations

import json

import pytest

from okcode.memory.models import MemoryAction, MemoryCategory, MemoryJob, MemoryScope
from okcode.memory.request import MemoryRequestFactory
from okcode.models import ChatMessage, Role


def _response() -> str:
    return json.dumps(
        {
            "operations": [
                {
                    "scope": "user",
                    "category": "preference",
                    "action": "create",
                    "name": "concise-style",
                    "summary": "回答风格",
                    "content": "用户偏好简洁回答。",
                }
            ],
            "user_index": [
                {
                    "name": "concise-style",
                    "category": "preference",
                    "summary": "偏好简洁回答",
                }
            ],
            "project_index": [],
        },
        ensure_ascii=False,
    )


def test_build_disables_tools_and_cache_and_contains_turn_and_indexes() -> None:
    request = MemoryRequestFactory().build(
        MemoryJob((ChatMessage(Role.USER, "请简洁回答"), ChatMessage(Role.ASSISTANT, "好"))),
        "# 用户索引\n- 旧偏好",
        "# 项目索引\n- 技术栈",
    )

    assert request.tools == ()
    assert request.cache.enabled is False
    assert "绝对禁止调用工具" in request.prompt.stable_system
    assert "请简洁回答" in request.messages[0].content
    assert "旧偏好" in request.messages[0].content
    assert "技术栈" in request.messages[0].content


def test_parse_returns_structured_update() -> None:
    update = MemoryRequestFactory().parse(_response())

    assert update.operations[0].scope is MemoryScope.USER
    assert update.operations[0].category is MemoryCategory.PREFERENCE
    assert update.operations[0].action is MemoryAction.CREATE
    assert update.user_index[0].name == "concise-style"


def test_parse_accepts_noop_without_duplicate_note() -> None:
    raw = json.loads(_response())
    raw["operations"][0].update({"action": "noop", "name": None, "summary": "", "content": ""})

    update = MemoryRequestFactory().parse(json.dumps(raw, ensure_ascii=False))

    assert update.operations[0].action is MemoryAction.NOOP


@pytest.mark.parametrize(
    "response",
    (
        "",
        "```json\n{}\n```",
        '{"operations":[],"user_index":[]}',
        '{"operations":[],"user_index":[],"project_index":[],"extra":true}',
        '{"operations":[{"scope":"bad"}],"user_index":[],"project_index":[]}',
    ),
)
def test_parse_rejects_non_contract_responses(response: str) -> None:
    with pytest.raises(ValueError):
        MemoryRequestFactory().parse(response)
