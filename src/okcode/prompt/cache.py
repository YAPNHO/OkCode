"""提示缓存策略与用量模型。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from okcode.tools.models import ToolDefinition


@dataclass(frozen=True, slots=True)
class PromptCachePolicy:
    """一次模型请求的提示缓存策略。"""

    enabled: bool = False
    ttl: str = "5m"


@dataclass(frozen=True, slots=True)
class PromptCacheUsage:
    """Provider 返回的真实提示缓存用量。"""

    read_tokens: int | None = None
    write_tokens: int | None = None
    available: bool = False

    @classmethod
    def unavailable(cls) -> PromptCacheUsage:
        """表示 Provider 未返回缓存相关字段。"""

        return cls()


def build_cache_key(stable_system: str, tools: Sequence[ToolDefinition]) -> str:
    """为稳定提示和工具定义生成不含动态上下文的摘要。"""

    payload = {
        "stable_system": stable_system,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
