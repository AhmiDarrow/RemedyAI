"""HTTP bridge for desktop host ↔ computer-use job queue."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from remedy.home import default_home


class JobCompleteRequest(BaseModel):
    ok: bool = True
    result: dict[str, Any] | None = None
    error: str | None = None


class HostHelloRequest(BaseModel):
    client: str = Field(default="desktop", description="desktop | webui")
    # Optional CSS bounds of the browser rail (from getBoundingClientRect)
    bounds: dict[str, float] | None = None
    scale: float | None = Field(default=None, description="devicePixelRatio")
    session_id: str | None = None


class CaptureRequest(BaseModel):
    """Screenshot full desktop or a region (physical pixels after optional scale)."""

    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    scale: float = 1.0
    label: str = "capture"


class A11yPushRequest(BaseModel):
    """One-shot a11y snapshot from in-page script (job_id is the secret)."""

    job_id: str
    elements: list[dict[str, Any]] = Field(default_factory=list)


def register_computer_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    _ = gateway, memory

    def _bridge():

        from remedy.core.computer.host_bridge import get_host_bridge

        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            try:
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
            except Exception:
                home = None
        return get_host_bridge(home or default_home())

    def _home_dir():
        from pathlib import Path

        if runtime is not None:
            h = getattr(getattr(runtime, "config", None), "home_dir", None)
            if h:
                return Path(h)
        try:
            from remedy.interfaces.config import load_config

            h = load_config().get("home_dir")
            if h:
                return Path(h)
        except Exception:
            pass
        return default_home()

    @app.post("/api/computer/host/hello")
    async def computer_host_hello(req: HostHelloRequest | None = None):
        """Bounds + session ping. Does not mark the rail driveable (jobs/next does)."""
        b = _bridge()
        # Soft touch only — real poller marks via jobs/ui routes.
        b.mark_host_alive(poller=False)
        if req and req.session_id:
            b.set_focused_session(req.session_id)
        if req and req.bounds:
            b.set_browser_bounds(req.bounds, scale=req.scale)
        return {
            "ok": True,
            "client": (req.client if req else "desktop"),
            "host_connected": b.host_connected(),
            "poller_required": True,
        }

    @app.get("/api/computer/host/status")
    async def computer_host_status():
        b = _bridge()
        return {
            "host_connected": b.host_connected(),
            "focused_session_id": b.focused_session_id(),
            "browser_bounds": b.get_browser_bounds(),
            "pending_jobs": b.pending_count(),
            "ui_command": b.peek_ui_command(),
            "jobs_root": str(b.root),
            "pending_hint": "Rust computer-host claims GET /api/computer/jobs/next",
            "host_driver": b.host_driver(),
        }

    @app.get("/api/computer/ui/command")
    async def computer_ui_command(
        take: bool = False, session_id: str = "", driver: str = ""
    ):
        """Open the Browser rail. take=1 is the Rust consumer; peek is not the poller."""
        b = _bridge()
        b.mark_host_alive(
            poller=bool(take),
            driver=(driver or "rust") if take else "",
        )
        if session_id.strip():
            b.set_focused_session(session_id)
        cmd = (
            b.take_ui_command(session_id=session_id or None)
            if take
            else b.peek_ui_command()
        )
        return {"command": cmd}

    @app.post("/api/computer/ui/command/ack")
    async def computer_ui_command_ack(job_id: str | None = None):
        b = _bridge()
        b.clear_ui_command(job_id=job_id)
        return {"ok": True}

    @app.post("/api/computer/capture")
    async def computer_capture(req: CaptureRequest):
        """Server-side screenshot on this PC (full or region). Used by host + tools."""
        from remedy.core.computer.desktop_os import native

        win = native()

        # GDI/PIL capture + PNG encode can take hundreds of ms — keep the
        # loop free so the host pollers and chat stream do not stall.
        def _grab():
            if (
                req.x is not None
                and req.y is not None
                and req.width is not None
                and req.height is not None
            ):
                return win.screenshot_region_png(
                    int(req.x),
                    int(req.y),
                    int(req.width),
                    int(req.height),
                    scale=float(req.scale or 1.0),
                )
            return win.screenshot_png()

        try:
            info = await asyncio.to_thread(_grab)
        except Exception as e:
            raise HTTPException(500, str(e)) from e
        return {"ok": True, "capture": info}

    @app.get("/api/computer/jobs/next")
    async def computer_job_next(
        exclude: str = "",
        only: str = "",
        session_id: str = "",
        wait_ms: int = 0,
        driver: str = "",
    ):
        """Desktop host claims the next pending browser job (or null).

        *exclude*: comma-separated actions to leave pending.
        *only*: if set, only claim these actions.
        *wait_ms*: block until enqueue or timeout (wake-on-enqueue; max 5000).

        Packaged desktop: Rust computer-host is the only poller.
        """
        b = _bridge()
        b.mark_host_alive(poller=True, driver=driver or "rust")
        skip: set[str] | None = None
        only_set: set[str] | None = None
        if exclude and str(exclude).strip():
            skip = {p.strip().lower() for p in str(exclude).split(",") if p.strip()}
        if only and str(only).strip():
            only_set = {p.strip().lower() for p in str(only).split(",") if p.strip()}
        if session_id.strip():
            b.set_focused_session(session_id)
        wait_s = min(5.0, max(0, int(wait_ms or 0)) / 1000.0)
        kwargs = {
            "exclude_actions": skip,
            "only_actions": only_set,
            "session_id": session_id or None,
            "wait_s": wait_s,
        }
        if wait_s > 0:
            job = await asyncio.to_thread(b.claim_next, **kwargs)
        else:
            job = b.claim_next(**kwargs)
        if job is None:
            return {"job": None}
        return {"job": job.to_dict()}

    @app.post("/api/computer/jobs/{job_id}/complete")
    async def computer_job_complete(job_id: str, req: JobCompleteRequest):
        b = _bridge()
        b.mark_host_alive()
        job = b.complete(
            job_id,
            ok=bool(req.ok),
            result=req.result,
            error=req.error,
        )
        if job is None:
            raise HTTPException(404, "job not found")
        return {"job": job.to_dict()}

    @app.post("/api/computer/jobs/{job_id}/cancel")
    async def computer_job_cancel(job_id: str):
        b = _bridge()
        job = b.cancel(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return {"job": job.to_dict()}

    def _a11y_cors(resp: Response, *, request: Request | None = None) -> Response:
        # Prefer no open CORS. Only echo a loopback Origin when present so
        # same-machine tooling can complete; never * + Private-Network.
        origin = ""
        if request is not None:
            origin = (request.headers.get("origin") or "").strip()
        if origin in (
            "http://127.0.0.1:7400",
            "http://localhost:7400",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "null",
        ):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        # Do NOT set Access-Control-Allow-Private-Network — browser PNA to
        # loopback a11y is closed. Desktop host completes via ureq (no CORS).
        return resp

    @app.options("/api/computer/a11y/push")
    async def computer_a11y_push_options(request: Request):
        return _a11y_cors(Response(status_code=204), request=request)

    @app.post("/api/computer/a11y/push")
    async def computer_a11y_push(req: A11yPushRequest, request: Request):
        """Complete a snapshot job from same-machine tooling (no open CORS *).

        Preferred path is Desktop/Tauri host complete (Bearer not required on
        loopback host routes). This push endpoint remains for legacy inject
        but no longer advertises * + Private-Network to the public web.
        """
        jid = (req.job_id or "").strip()
        # Full uuid hex is 32; reject short ids (spoof surface). Alnum only.
        if not jid or len(jid) < 32 or not jid.isalnum():
            raise HTTPException(400, "invalid job_id")
        b = _bridge()
        elements = [e for e in (req.elements or []) if isinstance(e, dict)][:120]
        job = b.complete_a11y_push(jid, elements)
        if job is None:
            raise HTTPException(404, "job not found or not a snapshot")
        return _a11y_cors(
            Response(
                content='{"ok":true}',
                media_type="application/json",
            ),
            request=request,
        )
