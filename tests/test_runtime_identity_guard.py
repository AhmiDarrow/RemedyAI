"""Guard: frozen / desktop-identity questions go through runtime_identity.

Raw ``sys.frozen`` / ``REMEDY_DESKTOP*`` checks scattered per-file each
answered a slightly different question — which is how a dev-checkout
Desktop got treated as a packaged install. New code must ask
``remedy.core.runtime_identity``; the allowlist below only shrinks.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "remedy"

# Files still allowed a raw check. Mostly PyInstaller asset-path mechanics
# (sys._MEIPASS layout), plus the authority module itself. Shrink, never grow.
ALLOWED = {
    "core/runtime_identity.py",
    "core/rg_binary.py",
    "core/workspace.py",
    "interfaces/uninstaller.py",
    "interfaces/updater.py",
    "runtime/bundle.py",
    "voice/runtime.py",
    "voice/service.py",
    "__init__.py",
}

_FROZEN_RE = re.compile(r"""getattr\(\s*\w*sys\w*\s*,\s*["']frozen["']""")
_DESKTOP_ENV_RE = re.compile(
    r"""environ(?:\.get\(\s*|\[\s*)["']REMEDY_DESKTOP"""
)


def _offending_files() -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        found = [
            m.group(0)
            for rx in (_FROZEN_RE, _DESKTOP_ENV_RE)
            for m in rx.finditer(text)
        ]
        if found:
            hits[rel] = found
    return hits


def test_no_new_raw_identity_checks() -> None:
    hits = _offending_files()
    assert not hits, (
        "Raw sys.frozen / REMEDY_DESKTOP* checks outside runtime_identity.py "
        f"(use is_frozen_install / is_desktop_sidecar / is_desktop_runtime): {hits}"
    )


def test_allowlist_entries_still_exist() -> None:
    """A deleted or cleaned file must leave the allowlist too."""
    for rel in ALLOWED:
        assert (SRC / rel).is_file(), f"stale allowlist entry: {rel}"
