"""HTTP bridge for desktop host ↔ computer-use job queue."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field


class JobCompleteRequest(BaseModel):
    ok: bool = True
    result: dict[str, Any] | None = None
    error: str | None = None


class HostHelloRequest(BaseModel):
    client: str = Field(default="desktop", description="desktop | webui")
    # Optional CSS bounds of the browser rail (from getBoundingClientRect)
    bounds: dict[str, float] | None = None
    scale: float | None = Field(default=None, description="devicePixelRatio")


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
        from pathlib import Path

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
        return get_host_bridge(home or Path.home() / ".remedy")

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
        return Path.home() / ".remedy"

    @app.post("/api/computer/host/hello")
    async def computer_host_hello(req: HostHelloRequest | None = None):
        """Desktop pings this while open (bounds + soft liveness).

        Does **not** alone mark the rail driveable — host_connected requires
        jobs/next or ui/command polling (see host_bridge.host_connected).
        """
        b = _bridge()
        # Soft touch only — real poller marks via jobs/ui routes.
        b.mark_host_alive(poller=False)
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
            "browser_bounds": b.get_browser_bounds(),
            "pending_jobs": b.pending_count(),
            "ui_command": b.peek_ui_command(),
            "jobs_root": str(b.root),
            "pending_hint": "claim via GET /api/computer/jobs/next",
        }

    @app.get("/api/computer/ui/command")
    async def computer_ui_command(take: bool = False):
        """Desktop polls this to open Browser rail (like Settings) without user action.

        *take*=1 atomically clears the command so hosts do not re-navigate the same URL.
        """
        b = _bridge()
        b.mark_host_alive(poller=True)
        if take:
            cmd = b.take_ui_command()
        else:
            cmd = b.peek_ui_command()
        return {"command": cmd}

    @app.post("/api/computer/ui/command/ack")
    async def computer_ui_command_ack(job_id: str | None = None):
        b = _bridge()
        b.clear_ui_command(job_id=job_id)
        return {"ok": True}

    @app.post("/api/computer/capture")
    async def computer_capture(req: CaptureRequest):
        """Server-side screenshot on this PC (full or region). Used by host + tools."""
        import sys

        if sys.platform != "win32":
            raise HTTPException(501, "capture requires Windows")
        from remedy.core.computer import desktop_win as win

        try:
            if (
                req.x is not None
                and req.y is not None
                and req.width is not None
                and req.height is not None
            ):
                info = win.screenshot_region_png(
                    int(req.x),
                    int(req.y),
                    int(req.width),
                    int(req.height),
                    scale=float(req.scale or 1.0),
                )
            else:
                info = win.screenshot_png()
        except Exception as e:
            raise HTTPException(500, str(e)) from e
        return {"ok": True, "capture": info}

    @app.get("/api/computer/jobs/next")
    async def computer_job_next(exclude: str = "", only: str = ""):
        """Desktop host claims the next pending browser job (or null).

        *exclude*: comma-separated actions to leave pending (SPA should pass
        ``exclude=navigate`` so Rust owns in-rail navigates via ui_command).
        *only*: if set, only claim these actions (Rust backup: ``only=navigate``).
        """
        b = _bridge()
        b.mark_host_alive(poller=True)
        skip: set[str] | None = None
        only_set: set[str] | None = None
        if exclude and str(exclude).strip():
            skip = {p.strip().lower() for p in str(exclude).split(",") if p.strip()}
        if only and str(only).strip():
            only_set = {p.strip().lower() for p in str(only).split(",") if p.strip()}
        job = b.claim_next(exclude_actions=skip, only_actions=only_set)
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

    def _a11y_cors(resp: Response) -> Response:
        # In-page snapshot POST from arbitrary https origins (job_id is the secret).
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        return resp

    @app.options("/api/computer/a11y/push")
    async def computer_a11y_push_options(request: Request):
        _ = request
        return _a11y_cors(Response(status_code=204))

    @app.post("/api/computer/a11y/push")
    async def computer_a11y_push(req: A11yPushRequest):
        """Complete a snapshot job from injected page JS (no API bearer)."""
        jid = (req.job_id or "").strip()
        if not jid or len(jid) < 8:
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
            )
        )
