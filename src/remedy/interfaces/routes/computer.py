"""HTTP bridge for desktop host ↔ computer-use job queue."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
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
        """Desktop pings this while open so tools know the rail host is live."""
        b = _bridge()
        b.mark_host_alive()
        if req and req.bounds:
            b.set_browser_bounds(req.bounds, scale=req.scale)
        return {
            "ok": True,
            "client": (req.client if req else "desktop"),
            "host_connected": b.host_connected(),
        }

    @app.get("/api/computer/host/status")
    async def computer_host_status():
        b = _bridge()
        return {
            "host_connected": b.host_connected(),
            "browser_bounds": b.get_browser_bounds(),
            "pending_hint": "claim via GET /api/computer/jobs/next",
        }

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
    async def computer_job_next():
        """Desktop host claims the next pending browser job (or null)."""
        b = _bridge()
        b.mark_host_alive()
        job = b.claim_next()
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
