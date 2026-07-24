"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from fastapi import FastAPI, Query, Request, Response

from remedy import __version__ as _remedy_version
from remedy.interfaces.api_models import (
    StatusResponse,
)

logger = logging.getLogger(__name__)


def register_status_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- health / status -----------------------------------------------------
    # Cache COUNT(*) results briefly — status is polled often from the desktop UI.
    _status_cache: dict[str, Any] = {"ts": 0.0, "payload": None}

    @app.get("/api/metrics")
    async def get_metrics(
        request: Request,
        format: str | None = Query(default=None, description="json (default) or prometheus"),
    ):
        """In-process metrics snapshot (JSON) or Prometheus text exposition.

        Use ``?format=prometheus`` or ``Accept: text/plain`` for Prometheus scrape.
        """
        from remedy.core.metrics import default_health, default_registry

        want_prom = (format or "").lower() in ("prometheus", "prom", "text")
        if not want_prom:
            accept = (request.headers.get("accept") or "").lower()
            want_prom = "text/plain" in accept and "application/json" not in accept

        if want_prom:
            body = default_registry.prometheus_text()
            return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

        health = await default_health.check()
        return {
            "version": _remedy_version,
            "metrics": default_registry.snapshot(),
            "health": health,
            "lines": default_registry.describe(),
        }

    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        now = time.time()
        cached = _status_cache.get("payload")
        if cached is not None and (now - float(_status_cache.get("ts") or 0)) < 2.0:
            # Refresh only volatile uptime fields.
            gw_stats = gateway.stats() if gateway else {"running": False}
            return StatusResponse(
                **{
                    **cached,
                    "uptime": gw_stats.get("uptime", cached.get("uptime", "N/A")),
                    "gateway": gw_stats,
                }
            )

        gw_stats = gateway.stats() if gateway else {"running": False}
        mem_count = 0
        skills_count = 0
        summary_sessions = 0
        chat_sessions = 0
        if memory:
            try:
                db = memory._ensure_db()
                mem_count = db.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
                summary_sessions = db.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
                chat_sessions = db.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
            except Exception:
                pass

        if runtime and hasattr(runtime, "skills") and runtime.skills:
            with contextlib.suppress(Exception):
                skills_count = len(runtime.skills.skills)

        payload = {
            "version": _remedy_version,
            "uptime": gw_stats.get("uptime", "N/A"),
            "gateway": gw_stats,
            "memory_entries": mem_count,
            "skills_count": skills_count,
            "sessions_count": summary_sessions,
            "chat_sessions_count": chat_sessions,
        }
        _status_cache["ts"] = now
        _status_cache["payload"] = {
            "version": _remedy_version,
            "uptime": payload["uptime"],
            "memory_entries": mem_count,
            "skills_count": skills_count,
            "sessions_count": summary_sessions,
            "chat_sessions_count": chat_sessions,
        }
        return StatusResponse(**payload)

