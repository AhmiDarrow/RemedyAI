"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Query, Request, Response

from remedy import __version__ as _remedy_version
from remedy.interfaces.api_models import (
    StatusResponse,
)
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

    @app.get("/api/turn-active")
    async def turn_active():
        """Is a chat turn on the wire in this serve process? Public, sub-ms.

        The desktop parent gates self-inject sidecar restarts on this — a
        crashed serve cannot answer HTTP, so it can never deadlock an apply
        the way a stale lock file could.
        """
        from remedy.core.stream_lock import any_stream_active

        return {"status": "ok", "active": any_stream_active()}

    @app.get("/api/notifications")
    async def list_notifications_route(
        unread_only: bool = Query(default=False),
        limit: int = Query(default=50),
    ):
        """Things Remedy surfaced while you were away (durable outbox)."""
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
        """Mark notification ids read (or all=true)."""
        from remedy.core.notify import mark_read, unread_count

        home = None
        with contextlib.suppress(Exception):
            home = (load_config() or {}).get("home_dir")
        body = payload or {}
        ids = [str(i) for i in (body.get("ids") or [])]
        changed = mark_read(ids, all_=bool(body.get("all")), home=home)
        return {"ok": True, "marked": changed, "unread": unread_count(home)}

    @app.get("/api/coordination/presence")
    async def coordination_presence(session_id: str | None = Query(default=None)):
        """Body coordination roster — the live muscles (sessions) and their holds.

        Feeds the desktop status surface so the user can see Remedy's whole body
        at work: which sessions are building, on what, holding which files.
        ``session_id`` (optional) marks that beacon as "you" in the payload.
        """
        import asyncio

        return await asyncio.to_thread(_coordination_presence_payload, session_id)

    def _coordination_presence_payload(session_id: str | None) -> dict[str, Any]:
        import os as _os

        beacons: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            from remedy.core.coordination import active_beacons

            now = time.time()
            for b in active_beacons():
                held = sorted(
                    _os.path.basename(p) for p in b.live_claims(now)
                )
                beacons.append(
                    {
                        "session_id": b.session_id,
                        "you": bool(session_id and b.session_id == session_id),
                        "muscle": b.muscle,
                        "project": _os.path.basename(
                            (b.project_path or "").rstrip("/\\")
                        )
                        or b.project_path
                        or "",
                        "project_path": b.project_path,
                        "goal": b.goal,
                        "phase": b.phase,
                        "held_files": held[:12],
                        "held_count": len(held),
                        "age_seconds": max(0, int(now - b.started_ts)),
                        "heartbeat_seconds_ago": max(
                            0, int(now - b.heartbeat_ts)
                        ),
                    }
                )
        return {"beacons": beacons, "count": len(beacons), "ts": time.time()}

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
        now = time.time()
        gw_stats = gateway.stats() if gateway else {"running": False}
        # Unauthenticated liveness (splash / status bar fallback) must not
        # open SQLite or report session/memory counts.
        if not _status_authed(request):
            return StatusResponse(
                version=_remedy_version,
                uptime=str(gw_stats.get("uptime", "N/A")),
                gateway={"running": bool(gw_stats.get("running"))},
            )

        cached = _status_cache.get("payload")
        if cached is not None and (now - float(_status_cache.get("ts") or 0)) < 2.0:
            # Refresh only volatile uptime fields.
            return StatusResponse(
                **{
                    **cached,
                    "uptime": gw_stats.get("uptime", cached.get("uptime", "N/A")),
                    "gateway": gw_stats,
                }
            )

        mem_count = 0
        skills_count = 0
        summary_sessions = 0
        chat_sessions = 0
        if memory:
            try:
                counts = getattr(memory, "status_counts", None)
                if counts is not None:
                    mem_count, summary_sessions, chat_sessions = await counts()
                else:
                    def _counts() -> tuple[int, int, int]:
                        db = memory._ensure_db()
                        return (
                            int(db.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]),
                            int(db.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]),
                            int(db.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]),
                        )

                    mem_count, summary_sessions, chat_sessions = await asyncio.to_thread(
                        _counts
                    )
            except Exception:
                pass

        if runtime and hasattr(runtime, "skills") and runtime.skills:
            with contextlib.suppress(Exception):
                skills_count = len(runtime.skills.skills)

        payload: dict[str, Any] = {
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

    @app.get("/api/self-improve")
    async def get_self_improve():
        """Unattended self-improve clock + last tick (no user prompt required)."""
        from remedy.core.self_inject import activity_snapshot

        home = None
        if runtime is not None:
            home = getattr(runtime, "home_dir", None) or getattr(
                getattr(runtime, "config", None), "home_dir", None
            )
        return activity_snapshot(home)

    # Serve boot instant (register time) — a python round finished before this
    # is running in this very process; one finished after it awaits a restart.
    _serve_started_utc = datetime.now(UTC).isoformat(timespec="seconds")

    @app.get("/api/self-inject/rounds")
    async def get_self_inject_rounds(limit: int = Query(default=20)):
        """Recent self-inject rounds with an honest per-round ``live`` verdict.

        ``live`` = the running serve/SPA actually executes the change;
        ``awaiting_restart`` = applied but this serve booted before the round;
        ``not_loaded`` = applied with no restart requested (frozen install).
        """
        from remedy.core.self_inject import read_ledger

        home = None
        if runtime is not None:
            home = getattr(runtime, "home_dir", None) or getattr(
                getattr(runtime, "config", None), "home_dir", None
            )

        def _live_state(r: dict) -> str:
            if str(r.get("status") or "") != "applied":
                return ""
            if str(r.get("tree") or "") == "desktop":
                return "live"  # SPA rebuilt in-round
            finished = str(r.get("finished_utc") or "")
            requested = bool(
                (r.get("detail") or {}).get("sidecar_restart_requested", True)
            )
            if finished and finished <= _serve_started_utc:
                return "live"
            return "awaiting_restart" if requested else "not_loaded"

        n = max(1, min(int(limit or 20), 100))
        rounds = read_ledger(home)[-n:][::-1]  # newest first
        for r in rounds:
            r["live_state"] = _live_state(r)
            # Keep the payload lean — the diff can be large and has its own audit trail.
            r.pop("diff", None)
        return {"serve_started_utc": _serve_started_utc, "rounds": rounds}

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

