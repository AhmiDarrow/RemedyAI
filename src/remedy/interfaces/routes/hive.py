"""Advanced hive inspector — roster, spawn, retire, assign."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

DEFAULT_BUDGET_STEPS = 8
MAX_BUDGET_STEPS = 64
DEFAULT_POSTS = 4
DEFAULT_LIVE_PULSES = 8
CADENCE_FORAGER = "forager"
CADENCE_POST = "post"
CADENCES = {CADENCE_FORAGER, CADENCE_POST}


class HiveRetireBody(BaseModel):
    hive_id: str = ""


class HiveSpawnBody(BaseModel):
    goal: str = ""
    cadence: str = CADENCE_FORAGER
    budget_steps: int = DEFAULT_BUDGET_STEPS
    pulse_s: int = 0


class HiveAssignBody(BaseModel):
    hive_id: str = ""
    goal: str = ""


def register_hive_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    def _store():
        from remedy.core.hive.store import get_hive_store
        from remedy.home import default_home

        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        return get_hive_store(home or default_home())

    @app.get("/api/hive/roster")
    async def hive_roster() -> dict[str, Any]:
        """Compact daughter roster for Automations tab. No tool logs."""
        store = _store()
        rows = [d.roster_line() for d in store.list_all()]
        posts = sum(1 for r in rows if r.get("cadence") == "post" and r.get("status") not in ("retired", "cancelled"))
        foragers = sum(
            1
            for r in rows
            if r.get("cadence") == "forager" and r.get("status") in ("pending", "running")
        )
        return {
            "daughters": rows[:40],
            "live_posts": posts,
            "live_foragers": foragers,
            "count": len(rows),
        }

    @app.post("/api/hive/spawn")
    async def hive_spawn(body: HiveSpawnBody) -> dict[str, Any]:
        """Spawn a new hive daughter bot directly from the Automations UI."""
        from contextlib import suppress

        goal = str(body.goal or "").strip()
        if not goal:
            return {"ok": False, "error": "goal is required"}

        cad = str(body.cadence or CADENCE_FORAGER).strip().lower()
        if cad not in CADENCES:
            cad = CADENCE_FORAGER

        store = _store()

        if cad == CADENCE_POST:
            if len(store.live_posts()) >= DEFAULT_POSTS:
                return {"ok": False, "error": f"At capacity ({DEFAULT_POSTS} standing posts). Retire one first."}
        else:
            live = store.live_pulses()
            if len(live) >= DEFAULT_LIVE_PULSES:
                return {"ok": False, "error": f"At capacity ({DEFAULT_LIVE_PULSES} live foragers). Collect or retire first."}

        budget = max(1, min(MAX_BUDGET_STEPS, int(body.budget_steps or DEFAULT_BUDGET_STEPS)))

        from remedy.core.hive.pulse import clamp_pulse_s, mark_next_pulse, schedule_post
        from remedy.core.hive.runner import schedule_forager

        pulse = clamp_pulse_s(body.pulse_s) if cad == CADENCE_POST else 0

        parent_sid = ""
        project_path = ""
        approval_mode = ""
        if runtime is not None:
            with suppress(Exception):
                parent_sid = getattr(runtime, "session_id", "") or ""
            with suppress(Exception):
                project_path = getattr(getattr(runtime, "config", None), "project_path", "") or ""
            with suppress(Exception):
                approval_mode = getattr(getattr(runtime, "config", None), "approval_mode", "") or ""

        daughter = store.hire(
            goal,
            cadence=cad,
            parent_session_id=parent_sid,
            project_path=project_path,
            approval_mode=approval_mode,
            budget_steps=budget,
            pulse_s=pulse,
        )

        with suppress(Exception):
            from remedy.core.turn_pipeline import bound_hive_capabilities
            daughter.journal["capabilities"] = bound_hive_capabilities()
            store.save(daughter)

        from remedy.core.hive.mother import announce_daughter
        announce_daughter(daughter, runtime)

        started = False
        if cad == CADENCE_POST:
            mark_next_pulse(daughter, due_now=True)
            store.save(daughter)
            started = schedule_post(runtime, daughter)
        else:
            started = schedule_forager(runtime, daughter)

        return {
            "ok": True,
            "hive_id": daughter.id,
            "cadence": daughter.cadence,
            "status": daughter.status,
            "started": started,
            "goal": daughter.goal,
        }

    @app.post("/api/hive/assign")
    async def hive_assign(body: HiveAssignBody) -> dict[str, Any]:
        """Reassign a standing post to a new goal."""
        hid = str(body.hive_id or "").strip()
        goal = str(body.goal or "").strip()
        if not hid:
            return {"ok": False, "error": "hive_id required"}
        if not goal:
            return {"ok": False, "error": "goal required"}

        store = _store()
        d = store.get(hid)
        if d is None:
            return {"ok": False, "error": "not found"}
        if d.cadence != CADENCE_POST:
            return {"ok": False, "error": "hive_assign only works on standing posts"}
        if d.status in ("retired", "cancelled"):
            return {"ok": False, "error": "bot is already retired"}

        d.goal = goal
        store.save(d)
        return {"ok": True, "hive_id": d.id, "goal": d.goal, "status": d.status}

    @app.post("/api/hive/retire")
    async def hive_retire(body: HiveRetireBody) -> dict[str, Any]:
        from remedy.core.hive.mother import retire_daughter

        hid = str(body.hive_id or "").strip()
        if not hid:
            return {"ok": False, "error": "hive_id required"}
        d = retire_daughter(hid, runtime)
        if d is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "hive_id": d.id, "status": d.status}
