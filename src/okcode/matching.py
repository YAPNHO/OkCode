"""权限和 Hook 共用的字符串匹配表达式。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase


class MatchKind(StrEnum):
    """正向匹配方式。"""

    EXACT = "exact"
    GLOB = "glob"
    REGEX = "regex"


@dataclass(frozen=True, slots=True, eq=False)
class MatchExpression:
    """一条可反向的匹配表达式。"""

    kind: MatchKind
    pattern: str
    negated: bool = False
    explicit: bool = True

    def matches(self, value: str) -> bool:
        """判断字符串是否命中表达式。"""

        if self.kind is MatchKind.EXACT:
            matched = value == self.pattern
        elif self.kind is MatchKind.GLOB:
            matched = fnmatchcase(value, self.pattern)
        else:
            matched = re.search(self.pattern, value) is not None
        return not matched if self.negated else matched

    def to_text(self) -> str:
        """生成可写回配置的稳定文本。"""

        if not self.explicit and not self.negated:
            return self.pattern
        prefix = self.kind.value + ":"
        value = prefix + self.pattern
        return "not:" + value if self.negated else value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.to_text() == other
        if not isinstance(other, MatchExpression):
            return NotImplemented
        return (
            self.kind == other.kind
            and self.pattern == other.pattern
            and self.negated == other.negated
            and self.explicit == other.explicit
        )


def parse_match_expression(text: object, location: str = "match") -> MatchExpression:
    """解析裸模式或 exact/glob/regex/not 前缀表达式。"""

    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{location} 必须是非空字符串。")
    value = text.strip()
    negated = False
    if value.startswith("not:"):
        negated = True
        value = value[4:]
        if value.startswith("not:"):
            raise ValueError(f"{location} 不能嵌套 not。")

    kind: MatchKind
    pattern: str
    if ":" in value:
        prefix, pattern = value.split(":", maxsplit=1)
        try:
            kind = MatchKind(prefix)
        except ValueError as exc:
            raise ValueError(f"{location} 的匹配类型无效：{prefix}") from exc
        if not pattern:
            raise ValueError(f"{location} 的匹配内容不能为空。")
        explicit = True
    else:
        pattern = value
        kind = MatchKind.GLOB if _has_glob_meta(pattern) else MatchKind.EXACT
        explicit = False

    if kind is MatchKind.REGEX:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{location} 的正则表达式无效。") from exc
    if kind is MatchKind.GLOB:
        _validate_glob_pattern(pattern, location)
    return MatchExpression(kind, pattern, negated, explicit)


def _has_glob_meta(value: str) -> bool:
    return any(character in value for character in "*?[")


def _validate_glob_pattern(pattern: str, location: str) -> None:
    """拒绝未闭合的 glob 字符类。"""

    class_open = False
    for character in pattern:
        if character == "[":
            if class_open:
                raise ValueError(f"{location} 的 glob 字符类无效。")
            class_open = True
        elif character == "]" and class_open:
            class_open = False
    if class_open:
        raise ValueError(f"{location} 的 glob 字符类未闭合。")
