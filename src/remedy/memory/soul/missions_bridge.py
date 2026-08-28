"""Mission × Soul — open pledges / threads auto-arm lightweight missions.

When the organism holds an unfinished relational or work pledge, surface it as
a durable mission checklist so capable muscle can finish what the soul remembers.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import Any

from remedy.core.metabolism.time_crystal import looks_like_job_resume_fact
from remedy.memory.soul.field import load_soul_field


def _normalize(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    t = re.sub(r"^(stay with:|continue:|ongoing focus:)\s*", "", t)
    return t[:160]


def collect_soul_mission_candidates(
    home: str | Any = None,
    *,
    limit: int = 5,
) -> list[dict[str, str]]:
    """Return pledge/open-thread candidates suitable for mission_start."""
    sf = load_soul_field(home)
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(goal: str, source: str) -> None:
        g = (goal or "").strip()
        if len(g) < 10:
            return
        key = _normalize(g)
        if key in seen or len(key) < 8:
            return
        seen.add(key)
        # Soft filter: skip pure emotional residue
        if re.search(r"(?i)^(lol|thanks|hi|hello)\b", g):
            return
        # Leftover "Stay with: Continue…" / resume-the-last-job lines are
        # session residue, not work she should arm as a mission.
        if looks_like_job_resume_fact(g):
            return
        out.append(
            {
                "goal": g[:200],
                "source": source,
                "steps": "1. Recall context via soul_recall\n"
                "2. Inspect project / files\n"
                "3. Implement or resolve\n"
                "4. Verify (tests or manual check)",
            }
        )

    for p in sf.pledges[-8:]:
        _add(p, "pledge")
    for d in getattr(sf, "future_dreams", None) or []:
        # Prefer the goal half of "Toward X: move"
        goal = d.split(":", 1)[0]
        goal = re.sub(r"(?i)^toward\s+", "", goal).strip()
        _add(goal or d, "dream")
    for t in sf.relational.open_threads[-8:]:
        _add(t, "open_thread")
    # Last episode open thread
    if sf.episodes:
        _add(sf.episodes[-1].open_thread, "episode")
    return out[: max(1, limit)]


def arm_soul_missions(
    runtime: Any = None,
    *,
    home: str | Any = None,
    session_id: str | None = None,
    max_new: int = 1,
    auto: bool = True,
) -> dict[str, Any]:
    """Create at most *max_new* missions from soul candidates if none active.

    Returns {ok, armed: [...], skipped: reason}.
    """
    home = home or (
        getattr(getattr(runtime, "config", None), "home_dir", None)
        if runtime is not None
        else None
    )
    sid = session_id
    if not sid and runtime is not None:
        with suppress(Exception):
            from remedy.core.turn_context import turn_session_id

            sid = turn_session_id(runtime)
        if not sid:
            sid = str(getattr(runtime, "_session_id", "") or "") or None

    from remedy.core.mission import MissionStore, create_mission, mission_summary

    store = MissionStore(home)
    latest = store.latest(sid)
    if latest is not None and latest.status == "active":
        return {
            "ok": True,
            "armed": [],
            "skipped": "active_mission",
            "active_id": latest.id,
            "active_goal": latest.goal[:120],
        }

    candidates = collect_soul_mission_candidates(home, limit=max_new + 2)
    if not candidates:
        return {"ok": True, "armed": [], "skipped": "no_candidates"}

    # Recent mission goals (no list() API — scan missions dir)
    recent_goals: list[str] = []
    with suppress(Exception):
        for fp in sorted(store.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[
            :16
        ]:
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("goal"):
                    recent_goals.append(str(data["goal"]))
            except Exception:
                continue

    # Normalise the recent goals once instead of per candidate — the goal
    # texts do not change while we walk the candidate list.
    recent_norm = {_normalize(g) for g in recent_goals}

    armed: list[dict[str, str]] = []
    for cand in candidates[: max(1, max_new)]:
        if _normalize(cand["goal"]) in recent_norm:
            continue
        steps = [ln.strip(" -*\t0123456789.") for ln in cand["steps"].splitlines() if ln.strip()]
        m = create_mission(
            cand["goal"],
            steps=steps,
            session_id=sid,
            verify_command=None,
            home=home,
        )
        # Tag mission meta if model supports it
        with suppress(Exception):
            if hasattr(m, "meta") and isinstance(getattr(m, "meta", None), dict):
                m.meta["source"] = f"soul:{cand['source']}"
                store.save(m)
        armed.append(
            {
                "id": m.id,
                "goal": m.goal[:160],
                "source": cand["source"],
                "summary": mission_summary(m)[:400],
            }
        )
        if len(armed) >= max_new:
            break

    # Soft inject into session brief
    if armed and runtime is not None:
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            brief = getattr(runtime, "_session_brief", None)
            if brief is None:
                runtime._session_brief = SessionBrief(session_id=sid or "")
                brief = runtime._session_brief
            g = armed[0]["goal"]
            if not getattr(brief, "intent", None):
                brief.intent = g[:500]
            tasks = list(getattr(brief, "open_tasks", None) or [])
            if g not in tasks:
                tasks.append(g[:200])
                brief.open_tasks = tasks[-20:]
            brief.touch()

    return {
        "ok": True,
        "armed": armed,
        "skipped": "" if armed else "all_duplicates",
        "auto": auto,
    }
