"""Body coordination — Remedy's proprioception across her muscles.

Multiple sessions can build at once (each possibly a different provider — Grok on
one task, Fable on another). They are all *muscles* of one Remedy sharing one
filesystem. This module is the shared sense of where her hands are: each active
session publishes a **beacon** (who / where / what files it holds) to a registry
under ``~/.remedy/coordination/``. Before a write hits disk, the writer claims
the path; if another **live** session already holds it, the write is refused
(block-and-coordinate) so no muscle overwrites another's work.

Design notes:
- Cross-process safe: state is a single JSON document guarded by an O_EXCL lock
  file with heartbeat + stale reclaim (same idiom as ``instance_lock``), plus an
  in-process ``RLock``. The enforced single-serve topology means contention is
  low; the lock keeps a rare CLI-vs-app race honest.
- Self-healing: a beacon with no heartbeat for ``BEACON_TTL`` is pruned, and a
  path claim not refreshed for ``CLAIM_TTL`` is released — a crashed or idle
  session never deadlocks the others.
- Advisory by construction: claims gate *writes* only; reads are never blocked.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# A session with no heartbeat for this long is considered gone (crash/idle).
BEACON_TTL = 180.0
# A path claim not refreshed by a write for this long is released.
CLAIM_TTL = 180.0
# The registry lock file is reclaimed if held (unrefreshed) longer than this.
_LOCK_STALE = 15.0

_thread_lock = threading.RLock()


def _home(home: str | Path | None = None) -> Path:
    # Same resolution idiom as build_ledger: explicit > REMEDY_HOME > ~/.remedy.
    base = home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    return Path(base).expanduser()


def _coord_dir(home: str | Path | None = None) -> Path:
    d = _home(home) / "coordination"
    with suppress(Exception):
        d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path(home: str | Path | None = None) -> Path:
    return _coord_dir(home) / "presence.json"


def _lock_path(home: str | Path | None = None) -> Path:
    return _coord_dir(home) / "presence.lock"


def norm_path(p: str | Path) -> str:
    """Normalized absolute path key (case-insensitive on Windows)."""
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(str(p))))
    except Exception:
        return os.path.normcase(str(p))


@dataclass
class SessionBeacon:
    """One active session's presence + the file paths it currently holds."""

    session_id: str
    muscle: str = ""  # provider/model label, e.g. "xai/grok-4-fast"
    project_path: str = ""
    goal: str = ""
    phase: str = "scout"
    pid: int = 0
    started_ts: float = field(default_factory=time.time)
    heartbeat_ts: float = field(default_factory=time.time)
    # normalized path -> last claim (write) timestamp
    claims: dict[str, float] = field(default_factory=dict)

    def is_live(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - float(self.heartbeat_ts or 0)) <= BEACON_TTL

    def live_claims(self, now: float | None = None) -> dict[str, float]:
        now = time.time() if now is None else now
        return {p: t for p, t in self.claims.items() if (now - float(t or 0)) <= CLAIM_TTL}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SessionBeacon:
        raw = raw or {}
        return cls(
            session_id=str(raw.get("session_id") or ""),
            muscle=str(raw.get("muscle") or ""),
            project_path=str(raw.get("project_path") or ""),
            goal=str(raw.get("goal") or "")[:300],
            phase=str(raw.get("phase") or "scout"),
            pid=int(raw.get("pid") or 0),
            started_ts=float(raw.get("started_ts") or time.time()),
            heartbeat_ts=float(raw.get("heartbeat_ts") or time.time()),
            claims={str(k): float(v) for k, v in dict(raw.get("claims") or {}).items()},
        )


# --- cross-process lock (O_EXCL + stale reclaim, like instance_lock) -------


def _acquire_file_lock(home: str | Path | None, *, timeout: float = 3.0) -> int | None:
    lp = _lock_path(home)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with suppress(Exception):
                os.write(fd, f"{os.getpid()}:{time.time():.3f}".encode())
            return fd
        except FileExistsError:
            # Reclaim a stale lock (holder crashed without releasing).
            with suppress(Exception):
                if (time.time() - lp.stat().st_mtime) > _LOCK_STALE:
                    with suppress(Exception):
                        lp.unlink()
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)
        except Exception:
            return None


def _release_file_lock(fd: int | None, home: str | Path | None) -> None:
    if fd is None:
        return
    with suppress(Exception):
        os.close(fd)
    with suppress(Exception):
        _lock_path(home).unlink()


def _read_beacons(home: str | Path | None) -> dict[str, SessionBeacon]:
    p = _registry_path(home)
    with suppress(Exception):
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {
            sid: SessionBeacon.from_dict(b)
            for sid, b in dict(raw.get("beacons") or {}).items()
            if sid
        }
    return {}


def _write_beacons(home: str | Path | None, beacons: dict[str, SessionBeacon]) -> None:
    p = _registry_path(home)
    payload = {"beacons": {sid: b.to_dict() for sid, b in beacons.items()}}
    tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    with suppress(Exception):
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(str(tmp), str(p))
        return
    with suppress(Exception):
        tmp.unlink()


def _prune(beacons: dict[str, SessionBeacon], now: float) -> None:
    dead = [sid for sid, b in beacons.items() if not b.is_live(now)]
    for sid in dead:
        beacons.pop(sid, None)
    # Drop expired individual claims from surviving beacons.
    for b in beacons.values():
        b.claims = b.live_claims(now)


def _test_isolated(home: str | Path | None) -> bool:
    """True when running under pytest with NO explicit registry home.

    Unit tests exercising unrelated code (begin_build_turn fixtures etc.) must
    not write beacons into the developer's real ~/.remedy registry. Tests that
    genuinely test coordination pass ``home=`` or set REMEDY_HOME.
    """
    if home is not None:
        return False
    if os.environ.get("REMEDY_HOME"):
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


@contextmanager
def _txn(home: str | Path | None = None) -> Iterator[dict[str, SessionBeacon]]:
    """Read-modify-write the registry under both the in-process and file lock."""
    if _test_isolated(home):
        # Isolated no-op: readers see empty, writers write nowhere.
        yield {}
        return
    with _thread_lock:
        fd = _acquire_file_lock(home)
        try:
            beacons = _read_beacons(home)
            _prune(beacons, time.time())
            yield beacons
            _write_beacons(home, beacons)
        finally:
            _release_file_lock(fd, home)


# --- public API ------------------------------------------------------------


def register(
    session_id: str,
    *,
    muscle: str = "",
    project_path: str = "",
    goal: str = "",
    phase: str = "scout",
    home: str | Path | None = None,
) -> None:
    """Announce / refresh a session's presence (beacon). Idempotent."""
    sid = (session_id or "").strip()
    if not sid:
        return
    now = time.time()
    with _txn(home) as beacons:
        b = beacons.get(sid)
        if b is None:
            b = SessionBeacon(session_id=sid, pid=os.getpid(), started_ts=now)
            beacons[sid] = b
        b.heartbeat_ts = now
        if muscle:
            b.muscle = muscle
        if project_path:
            b.project_path = str(project_path)
        if goal:
            b.goal = goal[:300]
        if phase:
            b.phase = phase


def heartbeat(
    session_id: str,
    *,
    phase: str | None = None,
    goal: str | None = None,
    project_path: str | None = None,
    muscle: str | None = None,
    home: str | Path | None = None,
) -> None:
    """Keep a beacon alive; optionally update phase/goal/project/muscle.

    ``muscle`` here self-corrects the label: register() may run before the
    per-turn LLM binding is set, but heartbeats fire mid-turn when the true
    provider/model is known.
    """
    sid = (session_id or "").strip()
    if not sid:
        return
    now = time.time()
    with _txn(home) as beacons:
        b = beacons.get(sid)
        if b is None:
            b = SessionBeacon(session_id=sid, pid=os.getpid(), started_ts=now)
            beacons[sid] = b
        b.heartbeat_ts = now
        if phase:
            b.phase = phase
        if goal:
            b.goal = goal[:300]
        if project_path:
            b.project_path = str(project_path)
        if muscle:
            b.muscle = muscle


def unregister(session_id: str, *, home: str | Path | None = None) -> None:
    """Remove a session's beacon and release all its claims (on session end)."""
    sid = (session_id or "").strip()
    if not sid:
        return
    with _txn(home) as beacons:
        beacons.pop(sid, None)


def claim_path(
    session_id: str,
    path: str | Path,
    *,
    muscle: str = "",
    project_path: str = "",
    goal: str = "",
    home: str | Path | None = None,
) -> SessionBeacon | None:
    """Claim ``path`` for this session before writing it.

    Returns the CONFLICTING beacon when another *live* session already holds the
    path (the caller must then block the write and coordinate). Returns ``None``
    on success (the claim is recorded / refreshed for this session).
    """
    sid = (session_id or "").strip()
    key = norm_path(path)
    if not sid or not key:
        return None
    now = time.time()
    with _txn(home) as beacons:
        for other_sid, b in beacons.items():
            if other_sid == sid:
                continue
            if not b.is_live(now):
                continue
            if key in b.live_claims(now):
                return b  # conflict — do NOT claim, do NOT overwrite
        me = beacons.get(sid)
        if me is None:
            me = SessionBeacon(session_id=sid, pid=os.getpid(), started_ts=now)
            beacons[sid] = me
        me.claims[key] = now
        me.heartbeat_ts = now
        if muscle:
            me.muscle = muscle
        if project_path:
            me.project_path = str(project_path)
        if goal:
            me.goal = goal[:300]
        return None


def release_path(
    session_id: str,
    path: str | Path | None = None,
    *,
    home: str | Path | None = None,
) -> None:
    """Release one path (or all of this session's claims when ``path`` is None)."""
    sid = (session_id or "").strip()
    if not sid:
        return
    with _txn(home) as beacons:
        b = beacons.get(sid)
        if b is None:
            return
        if path is None:
            b.claims = {}
        else:
            b.claims.pop(norm_path(path), None)


def active_beacons(
    *, exclude: str | None = None, home: str | Path | None = None
) -> list[SessionBeacon]:
    """All currently live beacons (optionally excluding one session)."""
    now = time.time()
    out: list[SessionBeacon] = []
    with _txn(home) as beacons:
        for sid, b in beacons.items():
            if exclude and sid == exclude:
                continue
            if b.is_live(now):
                out.append(b)
    out.sort(key=lambda b: b.started_ts)
    return out


def path_holder(
    path: str | Path, *, exclude: str | None = None, home: str | Path | None = None
) -> SessionBeacon | None:
    """The live session (if any, other than ``exclude``) holding ``path``."""
    key = norm_path(path)
    now = time.time()
    with _txn(home) as beacons:
        for sid, b in beacons.items():
            if exclude and sid == exclude:
                continue
            if b.is_live(now) and key in b.live_claims(now):
                return b
    return None


def coworkers_note(
    session_id: str, *, home: str | Path | None = None, limit: int = 4
) -> str:
    """A short plain-language line naming the other active muscles + their files.

    Injected into build turns so Remedy sees her coworkers and can divide work.
    Empty string when she is working alone.
    """
    now = time.time()
    others = list(active_beacons(exclude=session_id, home=home))
    if not others:
        return ""
    parts: list[str] = []
    for b in others[:limit]:
        who = b.muscle or "another muscle"
        where = os.path.basename(b.project_path.rstrip("/\\")) or b.project_path or "?"
        held = sorted(os.path.basename(p) for p in b.live_claims(now))
        held_txt = f" holding {', '.join(held[:5])}" if held else ""
        goal = f" — {b.goal}" if b.goal else ""
        parts.append(f"{who} in session {b.session_id[:8]} on {where}{goal} (phase {b.phase}){held_txt}")
    more = f" (+{len(others) - limit} more)" if len(others) > limit else ""
    return "Also working right now: " + "; ".join(parts) + more + "."
