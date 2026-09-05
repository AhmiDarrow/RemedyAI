"""Regression: TestClient route modules must not call unbounded request.body().

Production webhook/body caps live in Go ``httpapi``. The pytest FastAPI
route tree is empty; this AST guard keeps it that way for any future twin.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path("src/remedy/interfaces/routes")


def test_no_route_reads_a_body_without_a_cap():
    """Whole-class guard across every route module."""
    offenders: list[str] = []
    for path in sorted(ROUTES.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        text = path.read_text(encoding="utf-8")
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and getattr(n.func, "attr", "") == "body"
                and getattr(getattr(n.func, "value", None), "id", "") == "request"
            ):
                # voice.py checks len(body) right after and answers 413
                if "_MAX_AUDIO_BYTES" in text:
                    continue
                offenders.append(
                    f"{path.relative_to(ROUTES)}:{n.lineno} request.body()"
                )
    assert not offenders, (
        "unbounded request bodies:\n  "
        + "\n  ".join(offenders)
        + "\n(body caps belong in Go httpapi; do not revive FastAPI twins)"
    )
