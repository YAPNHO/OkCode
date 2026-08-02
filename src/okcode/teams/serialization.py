"""团队持久化 JSON 编解码辅助。"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints


def to_jsonable(value: object) -> object:
    """把团队 dataclass 转成可写入 JSON 的值。"""

    if is_dataclass(value):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def read_json(path: Path, default: object) -> object:
    """读取 JSON 文件，不存在时返回默认值。"""

    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: object) -> None:
    """原子写入 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(to_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("时间字段必须是 ISO 字符串。")
    return datetime.fromisoformat(value)


def parse_path(value: object) -> Path:
    if isinstance(value, Path):
        return value
    if not isinstance(value, str):
        raise ValueError("路径字段必须是字符串。")
    return Path(value)


def parse_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    raise ValueError("元组字段必须是列表。")


def coerce_dataclass(cls: type[Any], raw: dict[str, object]) -> Any:
    """按 dataclass 类型注解把 JSON 对象还原成实例。"""

    kwargs: dict[str, object] = {}
    hints = get_type_hints(cls)
    for field in fields(cls):
        if field.name not in raw:
            continue
        kwargs[field.name] = _coerce(hints.get(field.name, field.type), raw[field.name])
    return cls(**kwargs)


def _coerce(annotation: object, value: object) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is None:
        if isinstance(annotation, type):
            if issubclass(annotation, StrEnum):
                return annotation(value)
            if annotation is Path:
                return parse_path(value)
            if annotation is datetime:
                return parse_datetime(value)
            if is_dataclass(annotation):
                if not isinstance(value, dict):
                    raise ValueError("dataclass 字段必须是对象。")
                return coerce_dataclass(annotation, value)
        return value
    if origin is tuple:
        item_type = args[0] if args else object
        return tuple(_coerce(item_type, item) for item in parse_tuple(value))
    if origin is dict:
        return dict(value) if isinstance(value, dict) else {}
    if origin is list:
        item_type = args[0] if args else object
        return [_coerce(item_type, item) for item in parse_tuple(value)]
    if origin is type(None):
        return None
    if origin is not None and type(None) in args:
        if value is None:
            return None
        non_none = next(item for item in args if item is not type(None))
        return _coerce(non_none, value)
    return value
