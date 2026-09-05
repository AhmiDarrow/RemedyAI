"""One constant-time secret comparison, not three.

The local API bearer check, the generic webhook, and the Google Chat adapter
each carried their own copy — identical today, and three places for the next
fix to miss. A `!=` on a token leaks its prefix through timing; raising on a
bad type turns an auth check into a 500 instead of a 401.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from remedy.core.security import secret_equals


@pytest.mark.parametrize(
    ("a", "b", "expect"),
    [
        ("tok-abc", "tok-abc", True),
        ("tok-abc", "tok-xyz", False),
        ("tok", "tok-abc", False),          # shorter
        ("tok-abc-extra", "tok-abc", False),  # longer
        ("", "", False),   # an unset secret never matches
        ("", "x", False),
        ("x", "", False),
        (b"", b"", False),
        (b"abc", b"abc", True),
        (b"abc", "abc", True),
        ("tökén", "tökén", True),
        ("tökén", "token", False),
    ],
)
def test_it_answers_correctly(a, b, expect):
    assert secret_equals(a, b) is expect


@pytest.mark.parametrize(("a", "b"), [(None, None), (None, "x"), ("x", None)])
def test_none_never_raises_and_never_matches(a, b):
    """``(None, None)`` answered True — an auth check that passed when the
    expected secret was unset and the client sent nothing."""
    assert secret_equals(a, b) is False


def test_a_hostile_type_is_false_not_a_crash():
    """An auth check that raises is a 500, not a 401 — and a 500 says more."""
    assert secret_equals(object(), "x") is False  # type: ignore[arg-type]


def test_no_module_carries_its_own_copy():
    """Whole-class guard: the private helper must not come back."""
    offenders = []
    for path in sorted(Path("src/remedy").rglob("*.py")):
        if "bundled_skills" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_ct_eq":
                offenders.append(f"{path.relative_to('src/remedy')}:{n.lineno}")
    assert not offenders, (
        "local copies of the constant-time compare:\n  " + "\n  ".join(offenders)
        + "\n(use remedy.core.security.secret_equals)"
    )


def test_python_jwt_rs256_module_removed():
    """RS256 JWT verify for Teams webhooks lives in Go now."""
    import importlib.util

    assert importlib.util.find_spec("remedy.gateway") is None
