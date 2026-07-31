"""Skill 相关工具。"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Mapping, Sequence

from okcode.models import ChatMessage
from okcode.skills.activation import SkillActivationStore
from okcode.skills.catalog import SkillCatalog
from okcode.skills.models import (
    SkillArgumentError,
    SkillError,
    SkillExecutionMode,
    SkillHistoryMode,
    SkillToolManifest,
)
from okcode.skills.runner import SkillRunner
from okcode.tools.base import Tool
from okcode.tools.models import (
    JSONValue,
    PermissionTarget,
    ToolDefinition,
    ToolErrorCode,
    ToolFailure,
    ToolOutput,
    ToolSafety,
)
from okcode.tools.registry import ToolRegistry

LOAD_SKILL_TOOL_NAME = "load_skill"


class LoadSkillTool(Tool):
    """按需加载并激活完整 Skill SOP。"""

    def __init__(
        self,
        catalog: SkillCatalog,
        activation_store: SkillActivationStore,
        registry: ToolRegistry,
        *,
        runner: SkillRunner | None = None,
        history_provider: Callable[[], Sequence[ChatMessage]] | None = None,
        history_summary_provider: Callable[[], str | None] | None = None,
        refresh_callback: Callable[[], None] | None = None,
    ) -> None:
        self._catalog = catalog
        self._activation_store = activation_store
        self._registry = registry
        self._runner = runner
        self._history_provider = history_provider or (lambda: ())
        self._history_summary_provider = history_summary_provider or (lambda: None)
        self._refresh_callback = refresh_callback or self._catalog.refresh

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=LOAD_SKILL_TOOL_NAME,
            description="按名称加载并激活一个 Skill 的完整 SOP。",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "arguments": {"type": "object", "additionalProperties": True},
                    "history_override": {
                        "type": ["string", "null"],
                        "enum": ["none", "recent", "summary", "all_safe", None],
                    },
                },
            },
            timeout_seconds=120,
            safety=ToolSafety.READ_ONLY,
            permission_target=PermissionTarget(),
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        name = str(arguments["name"])
        try:
            self._refresh_callback()
            definition = self._catalog.load_definition(name)
            for tool in build_skill_tools(definition.dedicated_tools):
                if self._registry.get(tool.definition.name) is None:
                    self._registry.register(tool)
                else:
                    self._registry.replace(tool)
            raw_args = arguments.get("arguments")
            skill_args = raw_args if isinstance(raw_args, Mapping) else {}
            activation = self._activation_store.activate(definition, skill_args)
        except SkillArgumentError as exc:
            raise ToolFailure(ToolErrorCode.INVALID_ARGUMENTS, str(exc)) from exc
        except (SkillError, OSError, ValueError) as exc:
            raise ToolFailure(ToolErrorCode.INTERNAL_ERROR, str(exc)) from exc

        visible_tools = self._activation_store.visible_tool_names(
            (), load_skill_name=LOAD_SKILL_TOOL_NAME
        )
        data: dict[str, JSONValue] = {
            "skill": activation.name,
            "version": activation.version_id,
            "mode": activation.execution_mode.value,
            "visible_tools": list(visible_tools),
        }
        if activation.execution_mode is SkillExecutionMode.ISOLATED:
            if self._runner is None:
                raise ToolFailure(ToolErrorCode.INTERNAL_ERROR, "当前会话未配置独立 Skill 执行器。")
            history_override = arguments.get("history_override")
            try:
                history_mode = (
                    SkillHistoryMode(history_override)
                    if isinstance(history_override, str)
                    else activation.history_mode
                )
            except ValueError as exc:
                raise ToolFailure(
                    ToolErrorCode.INVALID_ARGUMENTS,
                    f"独立 Skill history_override 无效：{history_override}",
                ) from exc
            try:
                isolated_names = tuple(
                    sorted(
                        {
                            LOAD_SKILL_TOOL_NAME,
                            *activation.allowed_tools,
                            *activation.exposed_dedicated_tool_names,
                        }
                    )
                )
                result = await self._runner.run(
                    activation,
                    messages=tuple(self._history_provider()),
                    tools=self._registry.definitions_by_names(isolated_names),
                    history_mode=history_mode,
                    history_summary=self._history_summary_provider(),
                )
            except (SkillError, ValueError) as exc:
                raise ToolFailure(ToolErrorCode.INTERNAL_ERROR, str(exc), data) from exc
            data["summary"] = result.summary
            if not result.success:
                raise ToolFailure(
                    ToolErrorCode.INTERNAL_ERROR,
                    result.error_message or "独立 Skill 执行失败。",
                    data,
                )
            return ToolOutput(f"Skill {activation.name} 已独立执行完成。", data)
        return ToolOutput(f"Skill {activation.name} 已激活。", data)


class SkillScriptTool(Tool):
    """目录型 Skill 的脚本工具。"""

    def __init__(self, manifest: SkillToolManifest) -> None:
        self._manifest = manifest

    @property
    def definition(self) -> ToolDefinition:
        schema = json.loads(self._manifest.schema_path.read_text(encoding="utf-8"))
        return ToolDefinition(
            name=self._manifest.exposed_name,
            description=f"{self._manifest.description}\n此工具属于 Skill 专属工具。",
            input_schema=schema,
            timeout_seconds=self._manifest.timeout_seconds,
            safety=self._manifest.safety,
            permission_target=self._manifest.permission_target,
        )

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self._manifest.script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(arguments, ensure_ascii=False).encode("utf-8")),
                timeout=self._manifest.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ToolFailure(
                ToolErrorCode.TIMEOUT,
                f"Skill 脚本执行超时：{self._manifest.exposed_name}",
            ) from exc
        if process.returncode:
            raise ToolFailure(
                ToolErrorCode.COMMAND_FAILED,
                stderr.decode("utf-8", errors="replace") or "Skill 脚本执行失败。",
            )
        try:
            raw = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolFailure(
                ToolErrorCode.INTERNAL_ERROR, "Skill 脚本输出不是合法 JSON。"
            ) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("content"), str):
            raise ToolFailure(ToolErrorCode.INTERNAL_ERROR, "Skill 脚本输出缺少 content 字符串。")
        data = raw.get("data", {})
        if not isinstance(data, dict):
            raise ToolFailure(ToolErrorCode.INTERNAL_ERROR, "Skill 脚本输出 data 必须是对象。")
        return ToolOutput(raw["content"], data=data, truncated=bool(raw.get("truncated", False)))


def build_skill_tools(manifests: Sequence[SkillToolManifest]) -> tuple[Tool, ...]:
    return tuple(SkillScriptTool(manifest) for manifest in manifests)
