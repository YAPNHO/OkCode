"""Skill 激活快照和工具范围计算。"""

from __future__ import annotations

from collections.abc import Mapping

from okcode.skills.frontmatter import render_body
from okcode.skills.models import (
    SkillActivation,
    SkillArgumentError,
    SkillDefinition,
    SkillValidationError,
    normalize_skill_name,
)
from okcode.tools.models import JSONValue


class SkillActivationStore:
    """当前会话中已激活 Skill 的快照集合。"""

    def __init__(self) -> None:
        self._active: dict[str, SkillActivation] = {}

    def activate(
        self,
        definition: SkillDefinition,
        arguments: Mapping[str, JSONValue] | None = None,
    ) -> SkillActivation:
        metadata = definition.metadata
        args = dict(arguments or {})
        self.assert_model_compatible(metadata.model, replacing=metadata.name)
        try:
            rendered = render_body(definition.body, args)
        except SkillArgumentError:
            raise
        activation = SkillActivation(
            name=metadata.name,
            description=metadata.description,
            source=metadata.source,
            source_path=metadata.source_path,
            version_id=metadata.version_id,
            rendered_sop=rendered,
            arguments=args,
            allowed_tools=metadata.allowed_tools,
            exposed_dedicated_tool_names=tuple(
                tool.exposed_name for tool in definition.dedicated_tools
            ),
            execution_mode=metadata.execution_mode,
            history_mode=metadata.history_mode,
            model=metadata.model,
        )
        self._active[activation.key] = activation
        return activation

    def active(self) -> tuple[SkillActivation, ...]:
        """按首次激活顺序返回快照；重新加载不会改变既有位置。"""

        return tuple(self._active.values())

    def clear(self) -> None:
        self._active.clear()

    def render_active_section(self) -> str:
        parts: list[str] = []
        for item in self.active():
            parts.append(
                "\n".join(
                    (
                        f"### {item.name}",
                        f"来源：{item.source.value}",
                        f"版本：{item.version_id}",
                        f"说明：{item.description}",
                        "",
                        item.rendered_sop,
                    )
                )
            )
        return "\n\n".join(parts)

    def visible_tool_names(
        self,
        default_names: tuple[str, ...],
        *,
        load_skill_name: str,
    ) -> tuple[str, ...]:
        active = self.active()
        if not active:
            return tuple(sorted({*default_names, load_skill_name}))
        visible = {load_skill_name}
        for item in active:
            visible.update(item.allowed_tools)
            visible.update(item.exposed_dedicated_tool_names)
        return tuple(sorted(visible))

    def model_override(self) -> str | None:
        models = {item.model for item in self.active() if item.model}
        if not models:
            return None
        if len(models) > 1:
            raise SkillValidationError("已激活 Skill 存在多个不同模型覆盖。")
        return next(iter(models))

    def assert_model_compatible(self, model: str | None, *, replacing: str | None = None) -> None:
        if not model:
            return
        replacing_key = normalize_skill_name(replacing or "")
        models = {item.model for item in self.active() if item.model and item.key != replacing_key}
        if models and model not in models:
            raise SkillValidationError(f"Skill 模型覆盖冲突：{model} 与 {next(iter(models))}")
