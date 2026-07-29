from __future__ import annotations

from okcode.prompt import enhance_tool_definitions
from okcode.tools.defaults import build_default_registry
from okcode.tools.workspace import Workspace


def test_enhanced_tools_preserve_metadata_and_add_model_rules(tmp_path) -> None:
    original = build_default_registry(Workspace(tmp_path)).definitions()
    enhanced = enhance_tool_definitions(original)

    assert [tool.name for tool in enhanced] == [tool.name for tool in original]
    for before, after in zip(original, enhanced, strict=True):
        assert after.input_schema == before.input_schema
        assert after.timeout_seconds == before.timeout_seconds
        assert after.safety is before.safety
        assert before.description in after.description

    by_name = {tool.name: tool.description for tool in enhanced}
    assert "优先先读取" in by_name["read_file"]
    assert "优先使用此工具" in by_name["find_files"]
    assert "优先使用此工具" in by_name["search_code"]
    assert "写入前先读取" in by_name["write_file"]
    assert "编辑前必须先读取" in by_name["edit_file"]
    assert "验证、测试和诊断" in by_name["run_command"]
