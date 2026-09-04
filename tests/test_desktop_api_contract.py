"""Every endpoint the desktop calls must exist on Go remedy-runtime.

Production :7400 is Go httpapi. This contract is Go-only: an SPA path that
Go does not register fails the suite unless it is explicitly listed in
``KNOWN_GO_GAPS`` (Wave 2 cutover allowlist — shrink only, never fake success).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "src"
GO_HTTPAPI = ROOT / "native" / "go" / "httpapi"

API_PREFIX = "/api"
_GO_HANDLE = re.compile(
    r'HandleFunc\(\s*"([A-Z]+)\s+([^"]+)"',
)

# Desktop-called paths not yet on Go. Remove an entry in the same change that
# registers the HandleFunc (and preferably Go tests). Do not add soft stubs.
KNOWN_GO_GAPS: frozenset[str] = frozenset(
    {
        "/api/agents",
        "/api/app/command",
        "/api/commands",
        "/api/continuity/dashboard",
        "/api/coordination/presence",
        "/api/diagnostics",
        "/api/nanoswarm/status",
        "/api/nanoswarm/token/status",
        "/api/projects/scan",
        "/api/scratch",
        "/api/self-inject/rounds",
        "/api/sessions/bulk-project",
        "/api/sessions/import",
        "/api/sessions/{}/command",
        "/api/sessions/{}/export",
        "/api/sessions/{}/messages/{}/edit",
        "/api/sessions/{}/steer",
        "/api/sessions/{}/time-travel",
        "/api/sessions/{}/timeline",
        "/api/sessions/{}/todos",
        "/api/skills/archive-unused",
        "/api/skills/export",
        "/api/skills/import",
        "/api/skills/learning/summary",
        "/api/skills/metrics/reuse",
        "/api/skills/packs",
        "/api/skills/{}/body",
        "/api/skills/{}/feedback",
        "/api/skills/{}/quarantine",
        "/api/skills/{}/status",
    }
)

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


def _add_call(calls: dict[str, str], raw: str, where: str) -> None:
    path = raw.split("?")[0]
    if not path.startswith("/"):
        return
    if path.startswith("//") or "..." in path:
        return
    if path.startswith("/api/"):
        calls.setdefault(path, where)
    else:
        calls.setdefault(API_PREFIX + path, where)


def _desktop_calls() -> dict[str, str]:
    calls: dict[str, str] = {}
    for path in sorted(DESKTOP.rglob("*.ts*")):
        if "node_modules" in path.parts or path.name.endswith(".test.ts"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        where = str(path.relative_to(ROOT))
        for m in re.finditer(
            r"""apiFetch(?:<[^>]*>)?\(\s*[`'"]([^`'"]+)[`'"]""", text
        ):
            _add_call(calls, m.group(1), where)
        # Ternary / multiline: apiFetch<…>(cond ? `/i18n?…` : '/i18n')
        for m in re.finditer(
            r"""apiFetch[\s\S]{0,160}?[`'"](/[^`'"]+)[`'"]""", text
        ):
            _add_call(calls, m.group(1), where)
        for m in re.finditer(
            r"""(?:getApiBase\(\)|loopbackApi\(\))\s*\+\s*[`'"]([^`'"]+)[`'"]""",
            text,
        ):
            _add_call(calls, m.group(1), where)
        # Template: `${getApiBase()}/skills/export`
        for m in re.finditer(
            r"""\$\{(?:getApiBase|loopbackApi)\(\)\}(/[^`'"]*)[`'"]""",
            text,
        ):
            _add_call(calls, m.group(1), where)
        for m in re.finditer(
            r"""hostFetch\(\s*[`'"]([^`'"]+)[`'"]""", text
        ):
            _add_call(calls, m.group(1), where)
    return calls


def _go_routes() -> set[str]:
    served: set[str] = set()
    if not GO_HTTPAPI.is_dir():
        return served
    for path in sorted(GO_HTTPAPI.rglob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _GO_HANDLE.finditer(text):
            served.add(m.group(2))
    return served


def test_both_sides_are_discoverable():
    assert len(_desktop_calls()) > 50, "apiFetch call sites are no longer findable"
    assert len(_go_routes()) > 50, "Go HandleFunc declarations are no longer findable"


def test_no_desktop_call_hits_a_route_go_lacks():
    served = {_normalise(r) for r in _go_routes()}
    gaps = {_normalise(r) for r in KNOWN_GO_GAPS}
    stale = sorted(gaps & served)
    assert not stale, (
        "KNOWN_GO_GAPS entries are already on Go — remove them from the "
        "allowlist:\n  " + "\n  ".join(stale)
    )
    missing = sorted(
        f"{_normalise(call)}   (called from {where})"
        for call, where in _desktop_calls().items()
        if (_normalise(call) not in served) and (_normalise(call) not in gaps)
    )
    assert not missing, (
        "the desktop calls endpoints Go does not serve (and not in "
        "KNOWN_GO_GAPS) — these 404 on remedy-runtime:\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.parametrize(
    "route",
    [
        "/api/sessions",
        "/api/settings",
        "/api/skills",
        "/api/providers",
        "/api/i18n",
        "/api/memory/search",
        "/api/memory/facts",
        "/api/memory/persona-wipe",
        "/api/usage/summary",
        "/api/usage/series",
        "/api/usage/export",
        "/api/updates/check",
        "/api/providers/custom",
        "/api/providers/probe",
    ],
)
def test_the_routes_the_desktop_cannot_start_without(route):
    """Spot-check owner-facing routes required on Go for Desktop chrome."""
    assert route in {_normalise(r) for r in _go_routes()}
