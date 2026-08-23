"""Advanced hive inspector — roster only, never transcripts."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel


class HiveRetireBody(BaseModel):
    hive_id: str = ""


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
        """Compact daughter roster for Advanced diagnostics. No tool logs."""
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
