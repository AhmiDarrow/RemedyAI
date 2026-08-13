"""Project-level compound learning — cross-session fingerprint under ~/.remedy.

Stores lightweight stats so Remedy compresses earlier / pins patterns for
this workspace without a cloud brain.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()  # RLock: load_project_profile → load_all re-entrancy
# Hot-path cache: profiles.json rarely changes mid-turn; avoid re-read + parse.
_all_cache: dict[str, Any] | None = None
_all_cache_path: str | None = None
_all_cache_mtime_ns: int | None = None
_all_cache_size: int | None = None
_profile_hit = 0
_profile_miss = 0


def _home() -> Path:
    import os

    env = os.environ.get("REMEDY_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".remedy"


def project_id(project_path: str | None) -> str:
    p = str(project_path or "").strip()
    if not p:
        return "default"
    try:
        resolved = str(Path(p).expanduser().resolve()).lower()
    except Exception:
        resolved = p.lower()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def _store_path(*, create: bool = False) -> Path:
    """Path to profiles.json. mkdir only on write — never on hot-path load."""
    d = _home() / "project_learning"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d / "profiles.json"


def _invalidate_all_cache() -> None:
    global _all_cache, _all_cache_path, _all_cache_mtime_ns, _all_cache_size
    _all_cache = None
    _all_cache_path = None
    _all_cache_mtime_ns = None
    _all_cache_size = None


def clear_project_profile_cache() -> None:
    """Test / settings helper — drop in-memory profiles.json cache."""
    global _profile_hit, _profile_miss
    with _lock:
        _invalidate_all_cache()
        _profile_hit = 0
        _profile_miss = 0


def profile_cache_stats() -> dict[str, int]:
    """Cheap accuracy/perf counters for harness diagnostics."""
    with _lock:
        return {"hits": int(_profile_hit), "misses": int(_profile_miss)}


def _stat_ns_size(path: Path) -> tuple[int, int] | None:
    """Single stat syscall → (mtime_ns, size), or None if missing/unreadable."""
    try:
        st = path.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        return mtime_ns, int(st.st_size)
    except OSError:
        return None


def load_all() -> dict[str, Any]:
    """Load all project profiles (mtime/size cache — safe across process turns).

    Hot path: one ``stat`` when cache is warm (no mkdir, no is_file + stat double).
    """
    global _all_cache, _all_cache_path, _all_cache_mtime_ns, _all_cache_size
    global _profile_hit, _profile_miss
    with _lock:
        # Fast path: re-stat cached path only (no mkdir / path rebuild thrash)
        if _all_cache is not None and _all_cache_path is not None:
            path = Path(_all_cache_path)
            meta = _stat_ns_size(path)
            if meta is not None:
                mtime_ns, size = meta
                if (
                    _all_cache_mtime_ns == mtime_ns
                    and _all_cache_size == size
                ):
                    _profile_hit += 1
                    return _all_cache
            elif _all_cache_mtime_ns is None and _all_cache_size is None:
                # Cached empty (file absent) — still absent
                _profile_hit += 1
                return _all_cache

        path = _store_path(create=False)
        path_s = str(path)
        meta = _stat_ns_size(path)
        if meta is None:
            empty = {"version": 1, "projects": {}}
            _all_cache = empty
            _all_cache_path = path_s
            _all_cache_mtime_ns = None
            _all_cache_size = None
            _profile_miss += 1
            return empty
        mtime_ns, size = meta
        if (
            _all_cache is not None
            and _all_cache_path == path_s
            and _all_cache_mtime_ns == mtime_ns
            and _all_cache_size == size
        ):
            _profile_hit += 1
            return _all_cache
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "projects" in data:
                _all_cache = data
                _all_cache_path = path_s
                _all_cache_mtime_ns = mtime_ns
                _all_cache_size = size
                _profile_miss += 1
                return data
        except Exception:
            pass
        empty = {"version": 1, "projects": {}}
        _all_cache = empty
        _all_cache_path = path_s
        _all_cache_mtime_ns = mtime_ns
        _all_cache_size = size
        _profile_miss += 1
        return empty


def save_all(data: dict[str, Any]) -> None:
    path = _store_path(create=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    # Keep cache coherent with what we just wrote
    global _all_cache, _all_cache_path, _all_cache_mtime_ns, _all_cache_size
    with _lock:
        meta = _stat_ns_size(path)
        if meta is not None:
            mtime_ns, size = meta
            _all_cache = data
            _all_cache_path = str(path)
            _all_cache_mtime_ns = mtime_ns
            _all_cache_size = size
        else:
            _invalidate_all_cache()


def load_project_profile(project_path: str | None) -> dict[str, Any]:
    pid = project_id(project_path)
    with _lock:
        all_data = load_all()
        proj = all_data.get("projects", {}).get(pid)
        if not isinstance(proj, dict):
            proj = {
                "id": pid,
                "path": str(project_path or ""),
                "sessions": 0,
                "turns": 0,
                "compress_count": 0,
                "tokens_saved": 0,
                "re_explain_total": 0,
                "stuck_total": 0,
                "avg_quality": None,
                "prefer_earlier_compress": False,
                "pinned_constraints": [],
                "updated_at": 0,
            }
        proj["id"] = pid
        return dict(proj)


def record_session_end(
    project_path: str | None,
    quality_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge a finished session's quality into the project fingerprint."""
    q = quality_snapshot or {}
    pid = project_id(project_path)
    with _lock:
        all_data = load_all()
        projects = all_data.setdefault("projects", {})
        proj = projects.get(pid) if isinstance(projects.get(pid), dict) else {}
        if not proj:
            # Inline default — do not call load_project_profile (same lock → deadlock)
            proj = {
                "id": pid,
                "path": str(project_path or ""),
                "sessions": 0,
                "turns": 0,
                "compress_count": 0,
                "tokens_saved": 0,
                "re_explain_total": 0,
                "stuck_total": 0,
                "avg_quality": None,
                "prefer_earlier_compress": False,
                "pinned_constraints": [],
                "chapter": {"notes": [], "decisions": [], "updated_at": 0},
                "updated_at": 0,
            }
        proj["path"] = str(project_path or proj.get("path") or "")
        proj["sessions"] = int(proj.get("sessions") or 0) + 1
        proj["turns"] = int(proj.get("turns") or 0) + int(q.get("turns") or 0)
        proj["compress_count"] = int(proj.get("compress_count") or 0) + int(
            q.get("compress_count") or 0
        )
        proj["tokens_saved"] = int(proj.get("tokens_saved") or 0) + int(
            q.get("tokens_saved_by_compress") or 0
        )
        proj["re_explain_total"] = int(proj.get("re_explain_total") or 0) + int(
            q.get("re_explain_count") or 0
        )
        proj["stuck_total"] = int(proj.get("stuck_total") or 0) + int(
            q.get("stuck_signal_count") or 0
        )
        aq = q.get("avg_compress_quality")
        if aq is not None:
            prev = proj.get("avg_quality")
            if prev is None:
                proj["avg_quality"] = float(aq)
            else:
                proj["avg_quality"] = round(0.7 * float(prev) + 0.3 * float(aq), 3)
        # Prefer earlier compress if this project often hits strong nudges
        strong = int(q.get("strong_nudge_count") or 0)
        turns = max(1, int(q.get("turns") or 1))
        if strong / turns >= 0.15 or int(q.get("tokens_estimated_peak") or 0) > 80_000:
            proj["prefer_earlier_compress"] = True
        # Pin constraints from high re-explain sessions
        if int(q.get("re_explain_count") or 0) >= 2:
            pins = list(proj.get("pinned_constraints") or [])
            note = "User restates constraints often — trust Session Brief + /remember facts."
            if note not in pins:
                pins.append(note)
            proj["pinned_constraints"] = pins[-8:]
        proj["updated_at"] = time.time()
        projects[pid] = proj
        # Cap stored projects
        if len(projects) > 80:
            ordered = sorted(
                projects.items(),
                key=lambda kv: float(kv[1].get("updated_at") or 0),
            )
            for k, _ in ordered[: len(projects) - 80]:
                projects.pop(k, None)
        save_all(all_data)
        return dict(proj)


def suggest_harness_pct(
    profile: dict[str, Any] | None,
    default_min: float,
    default_max: float,
) -> tuple[float, float]:
    """Slightly earlier compress for chatty/long projects."""
    if not profile or not profile.get("prefer_earlier_compress"):
        return default_min, default_max
    # Soften thresholds ~8–10%
    mn = max(0.55, float(default_min) - 0.08)
    mx = max(mn + 0.05, float(default_max) - 0.06)
    return mn, mx


def pinned_constraints_block(project_path: str | None) -> str:
    prof = load_project_profile(project_path)
    pins = prof.get("pinned_constraints") or []
    if not pins:
        return ""
    lines = ["[Project continuity notes]"]
    for p in pins[:5]:
        lines.append(f"- {p}")
    return "\n".join(lines)


def record_project_chapter(
    project_path: str | None,
    *,
    note: str = "",
    decision: str = "",
) -> dict[str, Any]:
    """Remember a durable note/decision for this work chapter (not a one-off task)."""
    path = str(project_path or "").strip()
    if not path:
        return {}
    text_n = " ".join((note or "").split())[:200]
    text_d = " ".join((decision or "").split())[:200]
    if not text_n and not text_d:
        return {}
    pid = project_id(path)
    with _lock:
        all_data = load_all()
        projects = all_data.setdefault("projects", {})
        proj = projects.get(pid) if isinstance(projects.get(pid), dict) else {}
        if not proj:
            proj = {
                "id": pid,
                "path": path,
                "sessions": 0,
                "turns": 0,
                "pinned_constraints": [],
                "chapter": {"notes": [], "decisions": [], "updated_at": 0},
                "updated_at": 0,
            }
        chap = proj.get("chapter") if isinstance(proj.get("chapter"), dict) else {}
        notes = [str(x) for x in (chap.get("notes") or []) if str(x).strip()]
        decisions = [str(x) for x in (chap.get("decisions") or []) if str(x).strip()]
        if text_n and text_n not in notes:
            notes.append(text_n)
        if text_d and text_d not in decisions:
            decisions.append(text_d)
        chap = {
            "notes": notes[-12:],
            "decisions": decisions[-12:],
            "updated_at": time.time(),
        }
        proj["chapter"] = chap
        proj["path"] = path
        proj["updated_at"] = time.time()
        projects[pid] = proj
        save_all(all_data)
        return dict(chap)


def project_chapter_block(project_path: str | None, *, query: str = "") -> str:
    """Short 'this chapter of work' inject — survives Session Brief compress."""
    prof = load_project_profile(project_path)
    chap = prof.get("chapter") if isinstance(prof.get("chapter"), dict) else {}
    notes = [str(x).strip() for x in (chap.get("notes") or []) if str(x).strip()]
    decisions = [str(x).strip() for x in (chap.get("decisions") or []) if str(x).strip()]
    pins = [str(x).strip() for x in (prof.get("pinned_constraints") or []) if str(x).strip()]
    if not notes and not decisions and not pins:
        return ""
    folder = str(prof.get("path") or project_path or "").replace("\\", "/").rstrip("/").split("/")[-1]
    lines = [f"[This chapter{f' — {folder}' if folder else ''}]"]
    qtok = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower())) if query else set()

    def _score(text: str) -> int:
        if not qtok:
            return 0
        blob = text.lower()
        return sum(1 for t in qtok if t in blob)

    items: list[tuple[int, str, str]] = []
    for d in decisions:
        items.append((_score(d) + 1, "decided", d))
    for n in notes:
        items.append((_score(n), "note", n))
    for p in pins:
        items.append((_score(p), "pin", p))
    items.sort(key=lambda x: x[0], reverse=True)
    for _s, kind, text in items[:6]:
        lines.append(f"- ({kind}) {text[:180]}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
