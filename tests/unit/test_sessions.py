from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from okcode.models import ChatMessage, Role, ToolCall
from okcode.sessions.codec import complete_message_prefix, decode_record, encode_record
from okcode.sessions.models import SessionConfig
from okcode.sessions.store import SessionStore
from okcode.tools.models import ToolErrorCode, ToolExecutionResult


def _now() -> datetime:
    return datetime(2026, 7, 30, 10, 0, tzinfo=UTC)


def _store(tmp_path: Path, *, config: SessionConfig | None = None) -> SessionStore:
    return SessionStore(tmp_path, config, clock=_now, token_factory=lambda: "abcd")


def _result(call_id: str = "call-1", tool_name: str = "read_file") -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=call_id,
        tool_name=tool_name,
        success=True,
        content="结果",
        error_code=None,
        data={"path": "a.py"},
    )


def test_session_config_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError, match="保留"):
        SessionConfig(retention_days=0)
    with pytest.raises(ValueError, match="间隔"):
        SessionConfig(long_gap=timedelta())


def test_journal_is_lazy_and_generates_expected_id(tmp_path: Path) -> None:
    journal = _store(tmp_path).create_journal()

    assert re.fullmatch(r"20260730-100000-[0-9a-f]{4}", journal.session_id)
    assert journal.path.exists() is False


def test_journal_appends_each_message_as_valid_jsonl(tmp_path: Path) -> None:
    journal = _store(tmp_path).create_journal()
    messages = (ChatMessage(Role.USER, "问题"), ChatMessage(Role.ASSISTANT, "答案"))

    journal.append(messages)

    rows = journal.path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    assert [decode_record(row).message for row in rows] == list(messages)


def test_closed_journal_rejects_future_appends(tmp_path: Path) -> None:
    journal = _store(tmp_path).create_journal()

    journal.close()

    with pytest.raises(OSError):
        journal.append((ChatMessage(Role.USER, "question"),))


def test_codec_round_trips_anthropic_provider_state_and_tool_messages() -> None:
    call = ToolCall("call-1", "read_file", '{"path":"a.py"}')
    messages = (
        ChatMessage(
            Role.ASSISTANT,
            "先读取文件",
            tool_calls=(call,),
            provider_state=(
                {"type": "text", "text": "先读取文件"},
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
            ),
        ),
        ChatMessage(Role.TOOL, tool_results=(_result(),)),
    )

    restored = tuple(decode_record(encode_record(_now(), message)).message for message in messages)

    assert restored == messages
    assert restored[0].provider_state == [
        {"type": "text", "text": "先读取文件"},
        {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "a.py"}},
    ]
    assert complete_message_prefix(restored) == (messages, False)


def test_complete_prefix_truncates_unmatched_or_mismatched_tool_calls() -> None:
    call = ToolCall("call-1", "read_file", "{}")
    messages = (
        ChatMessage(Role.USER, "读取"),
        ChatMessage(Role.ASSISTANT, tool_calls=(call,)),
        ChatMessage(Role.TOOL, tool_results=(_result(tool_name="write_file"),)),
    )

    prefix, truncated = complete_message_prefix(messages)

    assert prefix == (messages[0],)
    assert truncated is True


def test_restore_skips_bad_line_and_truncates_incomplete_tail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    journal = store.create_journal()
    call = ToolCall("call-1", "read_file", "{}")
    valid = ChatMessage(Role.USER, "读取")
    incomplete = ChatMessage(Role.ASSISTANT, tool_calls=(call,))
    journal.append((valid, incomplete))
    with journal.path.open("a", encoding="utf-8") as file:
        file.write('{"not":"valid"}\n')
        file.write('{"broken"')

    restored = store.restore(journal.session_id)

    assert restored.messages == (valid,)
    assert restored.skipped_lines == 2
    assert restored.was_truncated is True


def test_list_scans_title_count_and_most_recent_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_journal()
    first.append((ChatMessage(Role.USER, "第一个会话"), ChatMessage(Role.ASSISTANT, "完成")))
    second_path = tmp_path / "sessions" / "20260730-100001-bcde.jsonl"
    second_path.parent.mkdir(exist_ok=True)
    second_path.write_text(
        encode_record(_now() + timedelta(minutes=1), ChatMessage(Role.USER, "第二个会话")) + "\n",
        encoding="utf-8",
    )

    descriptors = store.list_resumable()

    assert [item.id for item in descriptors] == ["20260730-100001-bcde", first.session_id]
    assert descriptors[0].title == "第二个会话"
    assert descriptors[1].message_count == 2
    assert not list((tmp_path / "sessions").glob("*.meta"))


def test_cleanup_expired_only_removes_old_jsonl(tmp_path: Path) -> None:
    store = _store(tmp_path, config=SessionConfig(retention_days=30))
    old = tmp_path / "sessions" / "20260601-100000-abcd.jsonl"
    current = tmp_path / "sessions" / "20260730-100000-bcde.jsonl"
    old.parent.mkdir()
    old.write_text(encode_record(_now(), ChatMessage(Role.USER, "旧")) + "\n", encoding="utf-8")
    current.write_text(encode_record(_now(), ChatMessage(Role.USER, "新")) + "\n", encoding="utf-8")
    old_time = (_now() - timedelta(days=31)).timestamp()
    os.utime(old, (old_time, old_time))

    assert store.cleanup_expired() == 1
    assert old.exists() is False
    assert current.exists() is True


def test_codec_rejects_unknown_error_code() -> None:
    result = _result()
    raw = encode_record(_now(), ChatMessage(Role.TOOL, tool_results=(result,)))
    assert '"error_code":null' in raw
    invalid = raw.replace('"error_code":null', '"error_code":"not_a_code"')

    with pytest.raises(ValueError, match="error_code"):
        decode_record(invalid)


def test_restore_rejects_unknown_session_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="会话"):
        _store(tmp_path).restore("20260730-100000-abcd")


def test_tool_result_error_code_round_trips(tmp_path: Path) -> None:
    result = ToolExecutionResult(
        tool_call_id="call-1",
        tool_name="read_file",
        success=False,
        content="不存在",
        error_code=ToolErrorCode.NOT_FOUND,
    )
    journal = _store(tmp_path).create_journal()
    journal.append((ChatMessage(Role.TOOL, tool_results=(result,)),))

    assert (
        decode_record(journal.path.read_text(encoding="utf-8").strip()).message.tool_result
        == result
    )
