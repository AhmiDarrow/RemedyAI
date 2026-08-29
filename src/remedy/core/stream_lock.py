"""Per-process stream-activity locks under ``<home>/locks``.

"A chat turn is on the wire" marker so the desktop parent (lib.rs
self_inject_apply_poller) never recycles serve mid-turn. Design:

- One file per process: ``locks/stream_active.<pid>``. A second runtime
  sharing the home (e.g. a gateway runner) can never unlink the desktop
  serve's lock when *its* turn ends — each process owns exactly one file.
- A daemon heartbeat refreshes the file's mtime while any session in this
  process streams, so readers treat an old mtime as a crashed writer and
  ignore it (``STALE_AFTER_S``) instead of deadlocking forever.
- The legacy shared ``stream_active`` name is no longer written; serve
  start still removes it (old writers) and ages out stale per-pid files.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import suppress
from pathlib import Path

STALE_AFTER_S = 120.0  # readers ignore locks older than this (crashed writer)
TOUCH_INTERVAL_S = 10.0  # heartbeat cadence — far inside STALE_AFTER_S

_mutex = threading.Lock()
_active: dict[Path, set[str]] = {}  # home -> streaming session keys
_heartbeat: threading.Thread | None = None


def _lock_path(home: Path) -> Path:
    return home / "locks" / f"stream_active.{os.getpid()}"


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def any_stream_active() -> bool:
    """True while any session in THIS process is streaming.

    Authoritative for the process itself — the ``/api/turn-active`` route
    serves this to the desktop parent, which prefers it over lock files
    (a crashed serve cannot answer HTTP, so it can never block an apply).
    """
    with _mutex:
        return any(_active.values())


def active_session_ids() -> list[str]:
    """Session keys currently streaming in this process (excludes ``_anon``)."""
    with _mutex:
        out: list[str] = []
        for sids in _active.values():
            out.extend(s for s in sids if s and s != "_anon")
        return out


def acquire_stream_lock(home: str | Path, session_key: str) -> None:
    """Mark *session_key* as streaming; write/refresh this process's lock file."""
    base = Path(home).expanduser()
    with _mutex:
        _active.setdefault(base, set()).add(session_key or "_anon")
        _ensure_heartbeat()
    with suppress(OSError):
        _touch(_lock_path(base))


def release_stream_lock(home: str | Path, session_key: str) -> None:
    """Unmark; unlink this process's lock file when its last stream ends."""
    base = Path(home).expanduser()
    with _mutex:
        sids = _active.get(base)
        empty = True
        if sids is not None:
            sids.discard(session_key or "_anon")
            empty = not sids
            if empty:
                _active.pop(base, None)
    if empty:
        with suppress(OSError):
            _lock_path(base).unlink(missing_ok=True)


def clear_stale_stream_locks(home: str | Path) -> None:
    """Serve start: drop the legacy shared file and any stale per-pid locks.

    Live locks (fresh mtime — e.g. a gateway runner mid-turn on the same
    home) are left alone; their heartbeat keeps them fresh.
    """
    locks = Path(home).expanduser() / "locks"
    with suppress(OSError):
        (locks / "stream_active").unlink(missing_ok=True)
    now = time.time()
    with suppress(OSError):
        for kid in locks.iterdir():
            if not kid.name.startswith("stream_active."):
                continue
            with suppress(OSError):
                if now - kid.stat().st_mtime > STALE_AFTER_S:
                    kid.unlink(missing_ok=True)


def _ensure_heartbeat() -> None:
    global _heartbeat
    if _heartbeat is not None and _heartbeat.is_alive():
        return
    t = threading.Thread(
        target=_heartbeat_loop, name="stream-lock-heartbeat", daemon=True
    )
    _heartbeat = t
    t.start()


def _heartbeat_loop() -> None:
    while True:
        time.sleep(TOUCH_INTERVAL_S)
        with _mutex:
            homes = [h for h, sids in _active.items() if sids]
        for h in homes:
            with suppress(OSError):
                _touch(_lock_path(h))
