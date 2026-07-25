"""Usage ledger + NanoToken status API routes."""

from __future__ import annotations

from fastapi import FastAPI, Query


def register_usage_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    _ = gateway  # unused

    @app.get("/api/usage/summary")
    async def usage_summary(
        range_days: float = Query(default=7.0, ge=0.01, le=3650.0),
        session_id: str | None = None,
    ):
        from remedy.core.usage_ledger import summary

        return summary(range_days=range_days, session_id=session_id)

    @app.get("/api/usage/series")
    async def usage_series(
        range_days: float = Query(default=30.0, ge=0.01, le=3650.0),
        group: str = Query(default="provider"),
    ):
        from remedy.core.usage_ledger import series

        g = "model" if str(group).lower() == "model" else "provider"
        return series(range_days=range_days, group=g)

    @app.get("/api/usage/session/{session_id}")
    async def usage_session(session_id: str):
        from remedy.core.usage_ledger import session_usage

        return session_usage(session_id)

    @app.get("/api/nanoswarm/token/status")
    async def token_nanobot_status():
        from remedy.nanoswarm.token_nanobot import get_token_nanobot

        return get_token_nanobot().status()

    @app.get("/api/continuity/dashboard")
    async def continuity_dashboard(session_id: str | None = None):
        """Harness + continuity quality metrics for the dashboard panel."""
        from remedy.core.session_quality import get_session_quality
        from remedy.nanoswarm import get_swarm
        from remedy.nanoswarm.token_nanobot import get_token_nanobot

        sid = (session_id or "").strip()
        if not sid and runtime is not None:
            sid = str(getattr(runtime, "_session_id", "") or "")
        quality = get_session_quality(sid or None).snapshot()
        swarm = get_swarm()
        token = get_token_nanobot()
        pat = swarm.pattern.for_session(sid or None).snapshot()
        remeasure = token.last_remeasure(sid or None)
        snap = getattr(runtime, "_last_context_snapshot", None) if runtime else None
        snap_pub = snap.to_public() if snap is not None and hasattr(snap, "to_public") else None
        prov = token.active_provider or getattr(runtime, "_llm_provider", None)
        mod = token.active_model or getattr(runtime, "_llm_model", None)
        health = swarm.health.snapshot(provider=prov, model=mod)
        goal = swarm.goal.snapshot(sid or None)
        scout = swarm.scout.status()
        return {
            "session_id": sid or "_default",
            "session_quality": quality,
            "pattern": pat,
            "goal": goal,
            "scout": scout,
            "health": health,
            "token": {
                "last_method": token.last_method,
                "last_estimate": token.last_estimate,
                "active_provider": prov,
                "active_model": mod,
                "last_remeasure": remeasure,
                "status": token.status(),
            },
            "context_snapshot": snap_pub,
            "harness_mode": getattr(runtime, "_harness_mode", "auto") if runtime else "auto",
            "swarm": {
                "event_count": swarm.status().get("event_count"),
                "last_event": swarm.status().get("last_event"),
            },
        }
