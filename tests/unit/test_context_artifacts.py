from __future__ import annotations

from pathlib import Path

import pytest

from okcode.context import ArtifactStore, ContextManager
from okcode.tools.models import ToolExecutionResult


def _result_with_json_size(size: int) -> ToolExecutionResult:
    empty = ToolExecutionResult("call-1", "read_file", True, "", None)
    result = ToolExecutionResult(
        "call-1",
        "read_file",
        True,
        "x" * (size - len(empty.to_json())),
        None,
    )
    assert len(result.to_json()) == size
    return result


def test_single_result_at_boundary_remains_inline(tmp_path: Path) -> None:
    manager = ContextManager(ArtifactStore(tmp_path, "boundary"))
    result = _result_with_json_size(50_000)

    normalized = manager.normalize_tool_results((result,))

    assert normalized == (result,)
    assert not manager._artifact_store.tool_results_dir.exists()  # type: ignore[attr-defined]


def test_large_result_is_externalized_with_readable_relative_path(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "session-a")
    manager = ContextManager(store)
    result = _result_with_json_size(50_001)

    normalized = manager.normalize_tool_results((result,))

    preview = normalized[0]
    artifact = preview.data["context_artifact"]
    assert isinstance(artifact, dict)
    relative_path = artifact["path"]
    assert isinstance(relative_path, str)
    assert relative_path.startswith(".okcode/context/session-a/tool-results/")
    assert (tmp_path / relative_path).read_text(encoding="utf-8") == result.to_json()
    assert result.content not in preview.content
    assert preview.tool_call_id == result.tool_call_id
    assert preview.tool_name == result.tool_name
    assert preview.success is result.success


def test_externalize_failure_leaves_callers_original_results_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "write-failure")
    manager = ContextManager(store)
    result = _result_with_json_size(50_001)

    def fail(*_: object) -> object:
        raise OSError("磁盘不可写")

    monkeypatch.setattr(store, "externalize", fail)

    with pytest.raises(OSError, match="磁盘不可写"):
        manager.normalize_tool_results((result,))

    assert result.content == _result_with_json_size(50_001).content
