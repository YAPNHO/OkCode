"""Skill Markdown frontmatter 解析与 SOP 渲染。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from okcode.skills.models import (
    SkillArgumentError,
    SkillExecutionMode,
    SkillHistoryMode,
    SkillParseError,
)
from okcode.tools.models import JSONValue

_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z0-9_-]+)\s*}}")
_ANY_PLACEHOLDER_RE = re.compile(r"{{\s*([^}]+?)\s*}}")


@dataclass(frozen=True, slots=True)
class ParsedSkillMarkdown:
    """解析后的入口 Markdown。"""

    name: str
    description: str
    tools: tuple[str, ...]
    mode: SkillExecutionMode
    history: SkillHistoryMode
    model: str | None
    body: str


def parse_skill_markdown(path: Path, *, include_body: bool = True) -> ParsedSkillMarkdown:
    """解析 Skill Markdown 文件。"""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(f"无法读取 Skill 文件：{path}") from exc
    frontmatter, body = _split_frontmatter(text, path)
    data = _load_yaml(frontmatter, path)
    parsed_body = body.strip()
    if not parsed_body:
        raise SkillParseError(f"Skill 正文不能为空：{path}")
    return ParsedSkillMarkdown(
        name=_string(data, "name", path),
        description=_string(data, "description", path),
        tools=_string_tuple(data, "tools", path),
        mode=_enum(data, "mode", SkillExecutionMode, path),
        history=_enum(data, "history", SkillHistoryMode, path),
        model=_optional_string(data, "model", path),
        body=parsed_body if include_body else "",
    )


def scan_has_body(path: Path) -> bool:
    """轻量确认 Markdown 是否有正文。"""

    try:
        text = path.read_text(encoding="utf-8")
        _, body = _split_frontmatter(text, path)
    except SkillParseError:
        raise
    except OSError as exc:
        raise SkillParseError(f"无法读取 Skill 文件：{path}") from exc
    return bool(body.strip())


def extract_placeholders(body: str) -> tuple[str, ...]:
    """提取 SOP 正文中的占位符名。"""

    invalid = [
        match.group(1).strip()
        for match in _ANY_PLACEHOLDER_RE.finditer(body)
        if not _PLACEHOLDER_RE.fullmatch(match.group(0))
    ]
    if invalid:
        raise SkillArgumentError(f"Skill 占位符名称无效：{', '.join(sorted(invalid))}")
    return tuple(sorted(set(_PLACEHOLDER_RE.findall(body))))


def render_body(body: str, arguments: Mapping[str, JSONValue] | None = None) -> str:
    """渲染 Skill SOP 正文。"""

    args = dict(arguments or {})
    placeholders = extract_placeholders(body)
    missing = [name for name in placeholders if name not in args]
    if missing:
        raise SkillArgumentError(f"LoadSkill 缺少参数：{', '.join(missing)}")

    def replace(match: re.Match[str]) -> str:
        value = args[match.group(1)]
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    rendered = _PLACEHOLDER_RE.sub(replace, body)
    extra = {key: value for key, value in args.items() if key not in placeholders}
    if extra:
        rendered += "\n\n## 用户传入参数\n"
        rendered += json.dumps(extra, ensure_ascii=False, sort_keys=True, indent=2)
    return rendered


def _split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    if not text.startswith("---"):
        raise SkillParseError(f"Skill 缺少 YAML frontmatter：{path}")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillParseError(f"Skill frontmatter 起始标记无效：{path}")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise SkillParseError(f"Skill 缺少 YAML frontmatter 结束标记：{path}")


def _load_yaml(frontmatter: str, path: Path) -> Mapping[str, object]:
    try:
        raw = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"Skill YAML 语法错误：{path}") from exc
    if not isinstance(raw, Mapping):
        raise SkillParseError(f"Skill frontmatter 必须是对象：{path}")
    return raw


def _string(data: Mapping[str, object], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillParseError(f"Skill 字段 {key} 必须是非空字符串：{path}")
    return value.strip()


def _optional_string(data: Mapping[str, object], key: str, path: Path) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SkillParseError(f"Skill 字段 {key} 必须是非空字符串或 null：{path}")
    return value.strip()


def _string_tuple(data: Mapping[str, object], key: str, path: Path) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SkillParseError(f"Skill 字段 {key} 必须是字符串列表：{path}")
    return tuple(item.strip() for item in value if item.strip())


def _enum(
    data: Mapping[str, object],
    key: str,
    enum_type: type[SkillExecutionMode] | type[SkillHistoryMode],
    path: Path,
) -> SkillExecutionMode | SkillHistoryMode:
    value = data.get(key)
    if not isinstance(value, str):
        raise SkillParseError(f"Skill 字段 {key} 必须是字符串：{path}")
    try:
        return enum_type(value.strip())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise SkillParseError(f"Skill 字段 {key} 只能是：{allowed}：{path}") from exc
