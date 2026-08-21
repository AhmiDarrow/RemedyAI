"""Bounded recency-ordered write ledger that survives compact.

Disk is the source of truth. Compact must not drop *which files she just
wrote* or the model reconstructs them from older chat (session 765c theme
revert). No bodies — path + tool only.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

_HOT_CAP = 24


def record_hot_write(runtime: Any, path: str, *, tool: str = "") -> None:
    """Move *path* to the tail of the process-local hot-write ring."""
    p = (path or "").strip().replace("\\", "/")
    if not p or runtime is None:
        return
    ring: list[dict[str, str]] = list(getattr(runtime, "_hot_writes", None) or [])
    ring = [r for r in ring if str(r.get("path") or "").replace("\\", "/") != p]
    ring.append({"path": p, "tool": str(tool or "")[:40]})
    runtime._hot_writes = ring[-_HOT_CAP:]
    brief = getattr(runtime, "_session_brief", None)
    if brief is not None:
        sync_hot_writes_into_brief(runtime)


def sync_hot_writes_into_brief(runtime: Any) -> None:
    """Copy the ring onto SessionBrief.hot_writes (paths, recency order)."""
    if runtime is None:
        return
    brief = getattr(runtime, "_session_brief", None)
    if brief is None:
        return
    paths: list[str] = []
    seen: set[str] = set()
    ring = list(getattr(runtime, "_hot_writes", None) or [])
    with suppress(Exception):
        from remedy.core.build_engine import get_build_state

        st = get_build_state(runtime)
        for p in list(getattr(st, "write_set", None) or []):
            ring.append({"path": str(p), "tool": "write"})
    for row in ring:
        p = str((row or {}).get("path") or "").strip().replace("\\", "/")
        if not p or p in seen:
            continue
        seen.add(p)
        paths.append(p)
    if hasattr(brief, "hot_writes"):
        brief.hot_writes = paths[-_HOT_CAP:]
        if hasattr(brief, "touch"):
            brief.touch()
