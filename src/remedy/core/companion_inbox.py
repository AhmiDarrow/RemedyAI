"""Drop-a-file inbox — new Desktop/Downloads files without the owner asking.

Polls common drop folders, remembers what was already seen, and surfaces
only *new* mocks/logs so a design or debug turn can start from the file.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

_WATCH_NAMES = ("Desktop", "Downloads")
_INTERESTING = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".svg",
        ".pdf",
        ".log",
        ".txt",
        ".md",
        ".json",
        ".zip",
        ".har",
        ".csv",
    }
)
_MAX_AGE_S = 6 * 3600
_MAX_NEW = 6


def _home(runtime: Any = None) -> Path:
    with suppress(Exception):
        h = getattr(getattr(runtime, "config", None), "home_dir", None)
        if h:
            return Path(h)
    return Path.home() / ".remedy"


def _seen_path(runtime: Any = None) -> Path:
    return _home(runtime) / "companion" / "inbox_seen.json"


def _load_seen(runtime: Any = None) -> dict[str, float]:
    fp = _seen_path(runtime)
    if not fp.is_file():
        return {}
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict):
        out: dict[str, float] = {}
        for k, v in raw.items():
            with suppress(Exception):
                out[str(k)] = float(v)
        return out
    return {}


def _save_seen(seen: dict[str, float], runtime: Any = None) -> None:
    fp = _seen_path(runtime)
    fp.parent.mkdir(parents=True, exist_ok=True)
    # cap
    if len(seen) > 400:
        items = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:300]
        seen = dict(items)
    fp.write_text(json.dumps(seen), encoding="utf-8")


def watch_roots(*, extra: list[Path] | None = None) -> list[Path]:
    home = Path.home()
    roots = [home / n for n in _WATCH_NAMES]
    if extra:
        roots.extend(extra)
    return [r for r in roots if r.is_dir()]


def poll_new_drops(
    runtime: Any = None,
    *,
    extra_roots: list[Path] | None = None,
    mark_seen: bool = True,
) -> list[dict[str, Any]]:
    """Return newly appeared interesting files since last poll."""
    seen = _load_seen(runtime)
    now = time.time()
    new: list[dict[str, Any]] = []
    for root in watch_roots(extra=extra_roots):
        with suppress(OSError):
            for p in root.iterdir():
                if not p.is_file() or p.name.startswith("."):
                    continue
                if p.suffix.lower() not in _INTERESTING:
                    continue
                key = str(p.resolve())
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if now - m > _MAX_AGE_S:
                    continue
                prev = seen.get(key)
                if prev is not None and m <= prev + 0.5:
                    continue
                age = max(0, int(now - m))
                ago = f"{age}s" if age < 60 else f"{age // 60}m"
                new.append(
                    {
                        "path": str(p),
                        "name": p.name,
                        "ago": ago,
                        "mtime": m,
                        "folder": root.name,
                    }
                )
                if mark_seen:
                    seen[key] = m
    new.sort(key=lambda r: float(r.get("mtime") or 0), reverse=True)
    new = new[:_MAX_NEW]
    if mark_seen and new:
        _save_seen(seen, runtime)
    return new


def format_inbox_block(drops: list[dict[str, Any]] | None) -> str:
    if not drops:
        return ""
    lines = ["## New drops (Desktop / Downloads — owner did not have to say so)"]
    for d in drops:
        lines.append(f"- `{d.get('folder')}/{d.get('name')}` ({d.get('ago')}) — {d.get('path')}")
    lines.append("Read or critique these if they match the current ask. Do not ignore a new mock/log.")
    return "\n".join(lines)
