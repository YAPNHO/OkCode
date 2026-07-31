from __future__ import annotations

import pytest

from okcode.matching import MatchKind, parse_match_expression


def test_match_expression_supports_bare_exact_and_glob() -> None:
    exact = parse_match_expression("git status")
    glob = parse_match_expression("git *")

    assert exact.kind is MatchKind.EXACT
    assert exact.matches("git status")
    assert not exact.matches("git diff")
    assert exact.to_text() == "git status"
    assert glob.kind is MatchKind.GLOB
    assert glob.matches("git diff")
    assert not glob.matches("npm test")
    assert glob.to_text() == "git *"


def test_match_expression_supports_explicit_prefixes_and_not() -> None:
    regex = parse_match_expression(r"regex:^src/.+\.py$")
    not_glob = parse_match_expression("not:glob:**/*.md")

    assert regex.matches("src/okcode/app.py")
    assert not regex.matches("README.md")
    assert regex.to_text() == r"regex:^src/.+\.py$"
    assert not_glob.matches("src/okcode/app.py")
    assert not not_glob.matches("docs/spec.md")
    assert not_glob.to_text() == "not:glob:**/*.md"


def test_match_expression_rejects_invalid_text() -> None:
    for value in ("", "unknown:x", "regex:[", "not:not:exact:x", "glob:[abc"):
        with pytest.raises(ValueError):
            parse_match_expression(value)
