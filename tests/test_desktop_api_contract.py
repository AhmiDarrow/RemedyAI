"""Every endpoint the desktop calls must exist on the Python side.

This gap is invisible to both toolchains: ``tsc`` type-checks the desktop
without knowing what the server serves, and pytest exercises the server without
knowing what the desktop asks for. A renamed or removed route shows up as a
feature that 404s in the built app, and nothing before that says a word.

99 apiFetch call sites, 159 declared routes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "src"
SERVER = ROOT / "src" / "remedy" / "interfaces"

#: apiFetch prepends this; the call sites pass the rest.
API_PREFIX = "/api"

pytestmark = pytest.mark.skipif(
    not DESKTOP.is_dir(), reason="desktop sources not in this tree"
)


def _normalise(path: str) -> str:
    """One spelling for a route, from either side.

    ``/sessions/${id}/messages`` and ``/sessions/{session_id}/messages`` are the
    same route. A ``${...}`` that does not follow a slash is a query-string or
    suffix splice, not a path segment, so it is dropped rather than turned into
    one.
    """
    path = path.split("?")[0]
    path = re.sub(r"(?<!/)\$\{[^}]*\}", "", path)
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def _desktop_calls() -> dict[str, str]:
    calls: dict[str, str] = {}
    for path in sorted(DESKTOP.rglob("*.ts*")):
        if "node_modules" in path.parts or path.name.endswith(".test.ts"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
            r"""apiFetch(?:<[^>]*>)?\(\s*[`'"]([^`'"]+)[`'"]""", text
        ):
            calls.setdefault(API_PREFIX + m.group(1), str(path.relative_to(ROOT)))
    return calls


def _served_routes() -> set[str]:
    served: set[str] = set()
    for path in sorted(SERVER.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in (
                "get", "post", "put", "delete", "patch", "websocket"
            ):
                continue
            if getattr(node.func.value, "id", "") not in ("app", "router"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    served.add(value)
    return served


def test_both_sides_are_discoverable():
    assert len(_desktop_calls()) > 50, "apiFetch call sites are no longer findable"
    assert len(_served_routes()) > 100, "route declarations are no longer findable"


def test_no_desktop_call_hits_a_route_that_does_not_exist():
    served = {_normalise(r) for r in _served_routes()}
    missing = sorted(
        f"{_normalise(call)}   (called from {where})"
        for call, where in _desktop_calls().items()
        if _normalise(call) not in served
    )
    assert not missing, (
        "the desktop calls endpoints the server does not serve — these 404 in "
        "the built app:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize(
    "route",
    [
        "/api/sessions",
        "/api/settings",
        "/api/skills",
        "/api/providers",
        "/api/updates/check",
    ],
)
def test_the_routes_the_desktop_cannot_start_without(route):
    """Spot-check the handful the app needs before it can show anything."""
    assert route in {_normalise(r) for r in _served_routes()}
