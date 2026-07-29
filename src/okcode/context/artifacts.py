"""工具结果的会话隔离外置存储。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from okcode.context.models import ToolResultArtifact
from okcode.tools.models import ToolExecutionResult


class ArtifactStore:
    """将完整工具结果原子写入当前工作区的受控目录。"""

    def __init__(self, workspace_root: Path, session_id: str | None = None) -> None:
        self._workspace_root = workspace_root.resolve()
        self._session_id = _safe_session_id(session_id)
        self._tool_results_dir = (
            self._workspace_root / ".okcode" / "context" / self._session_id / "tool-results"
        )

    @property
    def tool_results_dir(self) -> Path:
        """返回当前会话的工具结果目录，供测试和诊断使用。"""

        return self._tool_results_dir

    def externalize(self, result: ToolExecutionResult, ordinal: int) -> ToolResultArtifact:
        """原子写入完整稳定 JSON，并返回工作区相对路径。"""

        content = result.to_json()
        self._tool_results_dir.mkdir(parents=True, exist_ok=True)
        filename = f"result-{ordinal:04d}-{uuid4().hex[:12]}.json"
        destination = self._tool_results_dir / filename
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self._tool_results_dir,
                prefix=".pending-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            Path(temporary_name).replace(destination)
        except Exception:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            raise
        return ToolResultArtifact(
            relative_path=destination.relative_to(self._workspace_root).as_posix(),
            original_chars=len(content),
        )

    def preview_result(
        self,
        result: ToolExecutionResult,
        artifact: ToolResultArtifact,
    ) -> ToolExecutionResult:
        """保留工具调用元数据，用短预览替代完整内容。"""

        content = (
            "工具结果已外置。"
            f"完整内容位于：{artifact.relative_path}。"
            f"原始稳定 JSON 长度：{artifact.original_chars} 字符。"
            "需要细节时请使用读取工具重新读取该文件。"
        )
        return ToolExecutionResult(
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
            success=result.success,
            content=content,
            error_code=result.error_code,
            data={
                "context_artifact": {
                    "path": artifact.relative_path,
                    "original_chars": artifact.original_chars,
                }
            },
            truncated=True,
        )


def _safe_session_id(value: str | None) -> str:
    """避免会话标识影响外置目录层级或文件名。"""

    if value is None:
        return uuid4().hex
    normalized = re.sub(r"[^A-Za-z0-9_-]", "", value)
    return normalized or uuid4().hex
