"""Skill 文件发现、目录包解析和覆盖规则。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from okcode.skills.frontmatter import parse_skill_markdown
from okcode.skills.models import (
    SkillMetadata,
    SkillParseError,
    SkillParseIssue,
    SkillSourceKind,
    SkillToolManifest,
    SkillValidationError,
    normalize_skill_name,
)
from okcode.tools.models import PermissionTarget, PermissionTargetKind, ToolSafety


@dataclass(frozen=True, slots=True)
class SkillRoots:
    """三层 Skill 根目录。"""

    builtin: Path
    user: Path
    project: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> SkillRoots:
        package_root = Path(__file__).resolve().parent
        return cls(
            builtin=package_root / "builtin",
            user=Path.home() / ".okcode" / "skills",
            project=workspace_root / ".okcode" / "skills",
        )


@dataclass(frozen=True, slots=True)
class SkillDiscoveryResult:
    """一次扫描结果。"""

    effective: tuple[SkillMetadata, ...]
    overridden: tuple[SkillMetadata, ...]
    issues: tuple[SkillParseIssue, ...]


def discover_skills(roots: SkillRoots) -> SkillDiscoveryResult:
    """扫描三层目录并应用覆盖规则。"""

    discovered: list[SkillMetadata] = []
    issues: list[SkillParseIssue] = []
    for source, root in _root_items(roots):
        if not root.exists():
            continue
        for entry in _candidate_entries(root):
            try:
                discovered.append(_metadata_from_entry(entry, source))
            except SkillParseError as exc:
                issues.append(SkillParseIssue(entry, source, None, "error", str(exc)))

    by_source: dict[tuple[SkillSourceKind, str], SkillMetadata] = {}
    for item in discovered:
        key = (item.source, item.key)
        previous = by_source.get(key)
        if previous is not None:
            raise SkillValidationError(
                "同一来源存在同名 Skill "
                f"{item.name!r}：{previous.source_path} 与 {item.source_path}"
            )
        by_source[key] = item

    selected: dict[str, SkillMetadata] = {}
    overridden: list[SkillMetadata] = []
    for item in sorted(discovered, key=lambda value: (value.source.priority, value.key)):
        previous = selected.get(item.key)
        if previous is not None:
            overridden.append(previous)
        selected[item.key] = item

    return SkillDiscoveryResult(
        effective=tuple(sorted(selected.values(), key=lambda value: value.key)),
        overridden=tuple(sorted(overridden, key=lambda value: (value.key, value.source.priority))),
        issues=tuple(issues),
    )


def _root_items(roots: SkillRoots) -> tuple[tuple[SkillSourceKind, Path], ...]:
    return (
        (SkillSourceKind.BUILTIN, roots.builtin),
        (SkillSourceKind.USER, roots.user),
        (SkillSourceKind.PROJECT, roots.project),
    )


def _candidate_entries(root: Path) -> tuple[Path, ...]:
    entries: list[Path] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() == ".md":
            entries.append(path)
        elif path.is_dir() and (path / "SKILL.md").is_file():
            entries.append(path)
    return tuple(entries)


def _metadata_from_entry(entry: Path, source: SkillSourceKind) -> SkillMetadata:
    entry_path = entry / "SKILL.md" if entry.is_dir() else entry
    parsed = parse_skill_markdown(entry_path, include_body=False)
    package_dir = entry if entry.is_dir() else None
    dedicated = _load_tool_manifest(package_dir, parsed.name) if package_dir is not None else ()
    version = _version_id(entry_path, package_dir)
    return SkillMetadata(
        name=parsed.name,
        description=parsed.description,
        allowed_tools=parsed.tools,
        execution_mode=parsed.mode,
        history_mode=parsed.history,
        model=parsed.model,
        source=source,
        source_path=entry,
        entry_path=entry_path,
        package_dir=package_dir,
        version_id=version,
        has_body=True,
        dedicated_tools=dedicated,
    )


def _load_tool_manifest(package_dir: Path | None, skill_name: str) -> tuple[SkillToolManifest, ...]:
    if package_dir is None:
        return ()
    manifest_path = package_dir / "tools" / "tools.yaml"
    if not manifest_path.exists():
        return ()
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SkillParseError(f"目录型 Skill 工具 manifest 无法读取：{manifest_path}") from exc
    root = _mapping(raw, manifest_path)
    tools = root.get("tools")
    if not isinstance(tools, list):
        raise SkillParseError(f"目录型 Skill 工具 manifest 缺少 tools 列表：{manifest_path}")
    result: list[SkillToolManifest] = []
    local_names: set[str] = set()
    for index, raw_tool in enumerate(tools):
        item = _mapping(raw_tool, manifest_path)
        local_name = _string(item, "local_name", manifest_path)
        if local_name in local_names:
            raise SkillParseError(f"目录型 Skill 工具名称重复：{local_name}：{manifest_path}")
        local_names.add(local_name)
        schema_path = _inside(
            package_dir, package_dir / _string(item, "schema_path", manifest_path)
        )
        script_path = _inside(
            package_dir, package_dir / _string(item, "script_path", manifest_path)
        )
        if not schema_path.is_file() or not script_path.is_file():
            raise SkillParseError(f"目录型 Skill 工具文件不存在：{manifest_path} tools[{index}]")
        _validate_json_schema(schema_path)
        try:
            safety = ToolSafety(_string(item, "safety", manifest_path))
            timeout_seconds = float(item.get("timeout_seconds", 30))
        except (TypeError, ValueError) as exc:
            raise SkillParseError(
                f"目录型 Skill 工具配置无效：{manifest_path} tools[{index}]"
            ) from exc
        if timeout_seconds <= 0:
            raise SkillParseError(
                f"目录型 Skill 工具超时必须为正数：{manifest_path} tools[{index}]"
            )
        result.append(
            SkillToolManifest(
                local_name=local_name,
                exposed_name=f"skill__{normalize_skill_name(skill_name)}__{local_name}",
                description=_string(item, "description", manifest_path),
                schema_path=schema_path,
                script_path=script_path,
                timeout_seconds=timeout_seconds,
                safety=safety,
                permission_target=_permission_target(item),
            )
        )
    return tuple(result)


def _validate_json_schema(path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillParseError(f"目录型 Skill 工具 schema 无效：{path}") from exc
    if not isinstance(raw, dict):
        raise SkillParseError(f"目录型 Skill 工具 schema 必须是对象：{path}")


def _permission_target(item: Mapping[str, object]) -> PermissionTarget:
    raw = item.get("permission_target")
    if raw is None:
        return PermissionTarget()
    target = _mapping(raw, Path("permission_target"))
    try:
        kind = PermissionTargetKind(_string(target, "kind", Path("permission_target")))
    except ValueError as exc:
        raise SkillParseError("目录型 Skill 工具 permission_target.kind 无效。") from exc
    argument_name = target.get("argument_name")
    optional = bool(target.get("optional", False))
    return PermissionTarget(
        kind=kind,
        argument_name=argument_name if isinstance(argument_name, str) else None,
        optional=optional,
    )


def _inside(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate == resolved_root or resolved_root in resolved_candidate.parents:
        return resolved_candidate
    raise SkillParseError(f"目录型 Skill 工具路径越界：{candidate}")


def _mapping(value: object, path: Path) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SkillParseError(f"配置必须是对象：{path}")
    return value


def _string(data: Mapping[str, object], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillParseError(f"字段 {key} 必须是非空字符串：{path}")
    return value.strip()


def dedicated_tool_names(skills: Iterable[SkillMetadata]) -> set[str]:
    """返回所有目录型 Skill 专属工具名。"""

    return {tool.exposed_name for skill in skills for tool in skill.dedicated_tools}


def _version_id(entry_path: Path, package_dir: Path | None) -> str:
    """入口和目录型工具 manifest 变化时都生成新版本标识。"""

    paths = [entry_path]
    if package_dir is not None:
        manifest = package_dir / "tools" / "tools.yaml"
        if manifest.is_file():
            paths.append(manifest)
    parts = []
    for path in paths:
        stat = path.stat()
        parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)
