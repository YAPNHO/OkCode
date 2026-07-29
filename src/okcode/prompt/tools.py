"""模型可见工具描述的规则增强。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from okcode.tools.models import ToolDefinition

_GUIDANCE_BY_TOOL = {
    "read_file": "编辑文件或说明具体文件细节前，优先先读取目标文件的真实内容。",
    "find_files": "需要确认路径或文件存在性时，优先使用此工具，不要编造文件位置。",
    "search_code": "需要定位代码、符号或文本引用时，优先使用此工具并依据返回的路径和行号。",
    "write_file": "写入前先读取或明确确认目标内容，避免覆盖未知用户改动。",
    "edit_file": "编辑前必须先读取或确认目标内容；只基于唯一且真实的原文执行替换。",
    "run_command": "用于必要的验证、测试和诊断；说明目的，谨慎避免破坏性或不相关的副作用命令。",
}


def enhance_tool_definitions(tools: Sequence[ToolDefinition]) -> tuple[ToolDefinition, ...]:
    """返回追加模型行为规则后的工具定义，不修改注册表对象。"""

    return tuple(
        replace(
            tool,
            description=_enhanced_description(tool.description, _GUIDANCE_BY_TOOL.get(tool.name)),
        )
        for tool in tools
    )


def _enhanced_description(description: str, guidance: str | None) -> str:
    if not guidance or guidance in description:
        return description
    return f"{description}\n\n使用约束：{guidance}"
