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

    @app.post("/api/computer/host/hello")
    async def computer_host_hello(req: HostHelloRequest | None = None):
        """Desktop pings this while open so tools know the rail host is live."""
        b = _bridge()
        b.mark_host_alive()
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
            "pending_hint": "claim via GET /api/computer/jobs/next",
        }

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
