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
        ("", "", True),
        ("", "x", False),
        (b"abc", b"abc", True),
        (b"abc", "abc", True),
        ("tökén", "tökén", True),
        ("tökén", "token", False),
    ],
)
def test_it_answers_correctly(a, b, expect):
    assert secret_equals(a, b) is expect


@pytest.mark.parametrize(("a", "b"), [(None, None), (None, "x"), ("x", None)])
def test_none_never_raises(a, b):
    assert secret_equals(a, b) is (a is None and b is None)


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


def test_signature_verification_is_constant_time():
    import inspect

    from remedy.gateway.channels import jwt_rs256

    src = inspect.getsource(jwt_rs256.verify_rs256)
    assert "compare_digest" in src
    assert "return digest_info == expected" not in src


def test_a_forged_signature_still_fails():
    """The real property: the verifier rejects rubbish. Constant time must not
    have made it constant *true*."""
    from remedy.gateway.channels.jwt_rs256 import verify_rs256

    assert verify_rs256("a.b.c", n=3233, e=17) is False
    assert verify_rs256("", n=3233, e=17) is False
    assert verify_rs256("only.two", n=3233, e=17) is False
