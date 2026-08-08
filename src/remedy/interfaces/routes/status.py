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


def _agency_metrics_rollup(snap: dict[str, Any]) -> dict[str, Any]:
    """Sum labeled counters into a small trust/agency view for /api/metrics JSON."""
    totals: dict[str, float] = {
        "tool_calls": 0.0,
        "tool_success": 0.0,
        "tool_soft_errors": 0.0,
        "tool_errors": 0.0,
        "tool_batch_errors": 0.0,
        "tool_batch_exceptions": 0.0,
        "tool_recovery_nudges": 0.0,
        "skill_activate_ok": 0.0,
        "skill_auto_suggest": 0.0,
        "skill_run_ok": 0.0,
        "skill_run_error": 0.0,
    }
    name_map = {
        "remedy_tool_calls_total": "tool_calls",
        "remedy_tool_success_total": "tool_success",
        "remedy_tool_soft_errors_total": "tool_soft_errors",
        "remedy_tool_errors_total": "tool_errors",
        "remedy_tool_batch_errors_total": "tool_batch_errors",
        "remedy_tool_batch_exceptions_total": "tool_batch_exceptions",
        "remedy_tool_recovery_nudge_total": "tool_recovery_nudges",
        "remedy_skill_auto_suggest_inject_total": "skill_auto_suggest",
    }
    for c in snap.get("counters") or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "")
        val = float(c.get("value") or 0)
        if name in name_map:
            totals[name_map[name]] += val
        elif name == "remedy_skill_activate_total":
            labels = c.get("labels") or {}
            if str(labels.get("status") or "") == "ok":
                totals["skill_activate_ok"] += val
            else:
                totals["skill_activate_ok"] += val
        elif name == "remedy_skill_run_total":
            labels = c.get("labels") or {}
            st = str(labels.get("status") or "")
            if st == "ok":
                totals["skill_run_ok"] += val
            else:
                totals["skill_run_error"] += val
    return totals


def register_status_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- health / status -----------------------------------------------------
    # Cache COUNT(*) results briefly — status is polled often from the desktop UI.
    _status_cache: dict[str, Any] = {"ts": 0.0, "payload": None}

    @app.get("/api/ping")
    async def ping():
        """Ultra-light liveness for desktop connection indicator.

        No DB, no gateway stats — must stay sub-ms even when other handlers are busy
        with vision install / model discovery. Public (no auth).
        """
        return {"status": "ok", "version": _remedy_version, "ts": time.time()}

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
        snap = default_registry.snapshot()
        # Compact agency/trust rollup for operators (recovery + skills).
        agency = _agency_metrics_rollup(snap)
        return {
            "version": _remedy_version,
            "metrics": snap,
            "agency": agency,
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

    @app.get("/api/diagnostics")
    async def get_diagnostics(
        probe_providers: bool = Query(
            default=False,
            description="Also measure local provider HTTP latency (loopback only)",
        ),
    ):
        """Health diagnostics snapshot for the desktop Diagnostics screen.

        Aggregates Remedy API, RMB host, vision, hardware, computer host, and
        provider connectivity. Cheap by default (no remote provider chat probes).
        """
        from remedy.interfaces.diagnostics import collect_diagnostics

        return await collect_diagnostics(
            runtime=runtime,
            gateway=gateway,
            memory=memory,
            probe_providers=bool(probe_providers),
        )

