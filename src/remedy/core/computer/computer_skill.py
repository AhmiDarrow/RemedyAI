"""Computer skill memory — she learns how each site actually wants to be driven.

The build engine learns which repair strategy lands green and starts bold
sooner when boldness wins. This is the same spine for computer use: sites
differ — some resolve a click by its text label cleanly, some only by a
snapshot ref, some (canvas games, stubborn web apps) need raw coordinates.
Instead of guessing the same way every time, Remedy records which
*approach* actually worked per site and per action, and surfaces a steering
hint so she leads with what has been landing.

Pure, local, mtime-cached. Stores counts only — never page text, never a
URL's query/secrets (host origin only). Design mirrors build_persist's
learn→remember→adapt loop.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

SKILL_REL = Path("computer") / "skill.json"
MIN_EVIDENCE = 3          # don't steer on thin data
MAX_HOSTS = 200
_lock = threading.Lock()

# Approaches, cheapest/most-robust first (the sensible default order).
CLICK_APPROACHES = ("text", "ref", "coords")


def _home(home: str | Path | None = None) -> Path:
    import os

    if home:
        return Path(home).expanduser()
    env = (os.environ.get("REMEDY_HOME") or "").strip()
    return Path(env or "~/.remedy").expanduser()


def _path(home: str | Path | None = None) -> Path:
    return _home(home) / SKILL_REL


def approach_of(action: str, kwargs: dict[str, Any]) -> str:
    """The concrete approach a computer action used (for click-family tools)."""
    a = (action or "").lower()
    if a in ("click", "drag", "press_hold"):
        if str(kwargs.get("text") or "").strip():
            return "text"
        if str(kwargs.get("ref") or "").strip():
            return "ref"
        if kwargs.get("x") or kwargs.get("y") or kwargs.get("x2") or kwargs.get("y2"):
            return "coords"
        return "unknown"
    if a == "act":
        return "act"
    if a == "type":
        return "type"
    if a == "key":
        return "key"
    return a or "unknown"


def _load(home: str | Path | None = None) -> dict[str, Any]:
    p = _path(home)
    # Prefer the mtime cache; fall back to a direct read so a missing cache
    # module can never silently wipe persistence.
    with suppress(Exception):
        from remedy.memory.statecache import read_json_cached

        raw = read_json_cached(p)
        if isinstance(raw, dict) and isinstance(raw.get("hosts"), dict):
            return raw
    with suppress(Exception):
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("hosts"), dict):
            return raw
    return {"hosts": {}}


def _save(data: dict[str, Any], home: str | Path | None = None) -> None:
    hosts = data.get("hosts") or {}
    if len(hosts) > MAX_HOSTS:
        # keep most-recently-updated hosts
        items = sorted(
            hosts.items(), key=lambda kv: float(kv[1].get("ts") or 0), reverse=True
        )[:MAX_HOSTS]
        data["hosts"] = dict(items)
    p = _path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for _ in range(3):
        try:
            tmp.replace(p)
            return
        except OSError:
            time.sleep(0.02)
    with suppress(OSError):
        tmp.unlink()


def record_action(
    host: str,
    action: str,
    approach: str,
    ok: bool,
    home: str | Path | None = None,
) -> None:
    """Fold one action outcome into the per-host skill memory."""
    h = (host or "").strip().lower()
    if not h or h in ("desktop", "cmd", "posix", ""):
        # Desktop app clicks aren't a "site" — skill memory is per web host.
        if action.lower() in ("act",):
            return
        if not h:
            return
    key = f"{action.lower()}:{approach}"
    with _lock:
        # Deep-copy the loaded structure BEFORE mutating — _load may return the
        # statecache's shared read-only object, and lock-free readers
        # (steer_hint / preferred_click_approach) iterate it concurrently.
        import copy as _copy

        data = {"hosts": _copy.deepcopy(dict(_load(home).get("hosts") or {}))}
        hosts = data["hosts"]
        rec = hosts.setdefault(h, {"ts": 0.0, "approaches": {}})
        ap = rec["approaches"].setdefault(key, {"ok": 0, "fail": 0})
        if ok:
            ap["ok"] = int(ap.get("ok") or 0) + 1
        else:
            ap["fail"] = int(ap.get("fail") or 0) + 1
        rec["ts"] = time.time()
        _save(data, home)


def _rate(rec: dict[str, Any]) -> tuple[float, int]:
    ok = int(rec.get("ok") or 0)
    fail = int(rec.get("fail") or 0)
    n = ok + fail
    return (ok / n if n else 0.0), n


def preferred_click_approach(
    host: str, home: str | Path | None = None
) -> str | None:
    """The click approach with the best evidenced success rate for *host*.

    None when there isn't enough evidence to override the cheap default.
    """
    h = (host or "").strip().lower()
    if not h:
        return None
    data = _load(home)
    rec = (data.get("hosts") or {}).get(h)
    if not rec:
        return None
    best: str | None = None
    best_rate = -1.0
    for ap in CLICK_APPROACHES:
        stats = (rec.get("approaches") or {}).get(f"click:{ap}")
        if not stats:
            continue
        rate, n = _rate(stats)
        if n >= MIN_EVIDENCE and rate > best_rate:
            best_rate = rate
            best = ap
    return best


def steer_hint(
    host: str, home: str | Path | None = None, *, max_chars: int = 200
) -> str:
    """One line for the computer-use context: what has worked on this site.

    Empty until there is real evidence. Also names an approach to avoid when
    it has clearly been failing here.
    """
    h = (host or "").strip().lower()
    if not h:
        return ""
    data = _load(home)
    rec = (data.get("hosts") or {}).get(h)
    if not rec:
        return ""
    prefer: str | None = None
    prefer_rate = -1.0
    avoid: str | None = None
    for ap in CLICK_APPROACHES:
        stats = (rec.get("approaches") or {}).get(f"click:{ap}")
        if not stats:
            continue
        rate, n = _rate(stats)
        if n >= MIN_EVIDENCE:
            if rate > prefer_rate:
                prefer_rate = rate
                prefer = ap
            if rate <= 0.25:
                avoid = ap
    if prefer is None:
        return ""
    line = f"On {h}: clicking by {prefer} has worked best"
    if avoid and avoid != prefer:
        line += f"; avoid {avoid} (fails here)"
    line += "."
    return line[:max_chars]


MASTERY_MIN_ACTIONS = 6      # enough successful clicks to call it "known"
MASTERY_RATE = 0.8


def maybe_site_lesson(
    host: str, home: str | Path | None = None
) -> dict[str, Any] | None:
    """Return a one-time organism lesson when a site is newly mastered.

    When she has driven a site reliably (a preferred click approach with a
    strong success rate over enough actions), that competence becomes part
    of who she is — recorded once into organism memory, like a build win.
    Fires at most once per host (a per-host flag guards repeats).
    """
    h = (host or "").strip().lower()
    if not h or h in ("desktop", "cmd", "posix"):
        return None
    with _lock:
        data = _load(home)
        rec = (data.get("hosts") or {}).get(h)
        if not rec or rec.get("lesson_recorded"):
            return None
        best: str | None = None
        best_rate = -1.0
        best_n = 0
        for ap in CLICK_APPROACHES:
            stats = (rec.get("approaches") or {}).get(f"click:{ap}")
            if not stats:
                continue
            rate, n = _rate(stats)
            ok = int(stats.get("ok") or 0)
            if ok >= MASTERY_MIN_ACTIONS and rate >= MASTERY_RATE and rate > best_rate:
                best, best_rate, best_n = ap, rate, ok
        if best is None:
            return None
        # Mark so this milestone is recorded only once.
        hosts = dict(data.get("hosts") or {})
        hosts[h] = {**rec, "lesson_recorded": True}
        _save({"hosts": hosts}, home)
    return {
        "outcome": "green",
        "tree": "computer",
        "summary": f"Learned to drive {h} reliably — click by {best} works ({best_n} clean).",
        "gate_detail": f"host={h} approach={best}",
    }


def _host_from_url(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return ""
    with suppress(Exception):
        if "://" in s:
            from urllib.parse import urlparse

            return (urlparse(s).hostname or "").lower()
    return s.split("/", 1)[0].lower()


def _skill_host_hint(runtime: Any) -> str:
    """Steer hint for the site the rail is currently on (context injection)."""
    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    url = ""
    with suppress(Exception):
        from remedy.core.computer.host_bridge import get_host_bridge

        url = get_host_bridge(home).last_navigate_url() or ""
    host = _host_from_url(url)
    if not host:
        return ""
    return steer_hint(host, home)
