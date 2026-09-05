"""TestClient-only: ping/status/turn-active stubs + notifications/metrics/self-improve.

Go ``remedy-runtime`` owns production ``:7400`` ping/status/turn-active,
diagnostics, coordination/presence, and self-inject/rounds. Minimal stubs
remain so auth/CORS/perf TestClient suites can still exercise create_app.
"""
from __future__ import annotations

import contextlib
import hmac
import logging
import time
from typing import Any

from fastapi import FastAPI, Query, Request, Response

from remedy import __version__ as _remedy_version
from remedy.interfaces.api_models import StatusResponse
from remedy.interfaces.config import load_config

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
    """Register TestClient stubs (closes over runtime/gateway/memory)."""
    _ = memory

    @app.get("/api/ping")
    async def ping():
        """Ultra-light liveness stub for TestClient (Go owns production)."""
        from remedy.runtime.native_runtime import native_runtime_status

        return {
            "status": "ok",
            "version": _remedy_version,
            "ts": time.time(),
            "native_runtime": native_runtime_status(probe=False),
        }

    @app.get("/api/turn-active")
    async def turn_active():
        """Public stream-lock stub for TestClient (Go owns production)."""
        from remedy.core.stream_lock import any_stream_active

        return {"status": "ok", "active": any_stream_active()}

    @app.get("/api/notifications")
    async def list_notifications_route(
        unread_only: bool = Query(default=False),
        limit: int = Query(default=50),
    ):
        from remedy.core.notify import list_notifications, unread_count

        home = None
        with contextlib.suppress(Exception):
            home = (load_config() or {}).get("home_dir")
        items = list_notifications(
            unread_only=bool(unread_only), limit=int(limit or 50), home=home
        )
        return {
            "notifications": [n.to_dict() for n in items],
            "unread": unread_count(home),
            "count": len(items),
        }

    @app.post("/api/notifications/read")
    async def mark_notifications_read(payload: dict[str, Any] | None = None):
        from remedy.core.notify import mark_read, unread_count

        home = None
        with contextlib.suppress(Exception):
            home = (load_config() or {}).get("home_dir")
        body = payload or {}
        ids = [str(i) for i in (body.get("ids") or [])]
        changed = mark_read(ids, all_=bool(body.get("all")), home=home)
        return {"ok": True, "marked": changed, "unread": unread_count(home)}

    @app.get("/api/metrics")
    async def get_metrics(
        request: Request,
        format: str | None = Query(default=None, description="json (default) or prometheus"),
    ):
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
        agency = _agency_metrics_rollup(snap)
        return {
            "version": _remedy_version,
            "metrics": snap,
            "agency": agency,
            "health": health,
            "lines": default_registry.describe(),
        }

    def _status_authed(request: Request) -> bool:
        expected = str(getattr(request.app.state, "api_key", "") or "")
        if not expected:
            return True
        auth = request.headers.get("Authorization") or ""
        want = f"Bearer {expected}"
        if len(auth.encode("utf-8")) != len(want.encode("utf-8")):
            hmac.compare_digest(want.encode("utf-8"), want.encode("utf-8"))
            return False
        return hmac.compare_digest(auth.encode("utf-8"), want.encode("utf-8"))

    @app.get("/api/status", response_model=StatusResponse)
    async def get_status(request: Request):
        """Minimal status stub for TestClient auth/CORS suites (Go owns production)."""
        gw_stats = gateway.stats() if gateway else {"running": False}
        if not _status_authed(request):
            return StatusResponse(
                version=_remedy_version,
                uptime=str(gw_stats.get("uptime", "N/A")),
                gateway={"running": bool(gw_stats.get("running"))},
            )
        return StatusResponse(
            version=_remedy_version,
            uptime=str(gw_stats.get("uptime", "N/A")),
            gateway=gw_stats,
        )

    @app.get("/api/self-improve")
    async def get_self_improve():
        from remedy.core.self_inject import activity_snapshot

        home = None
        if runtime is not None:
            home = getattr(runtime, "home_dir", None) or getattr(
                getattr(runtime, "config", None), "home_dir", None
            )
        return activity_snapshot(home)
