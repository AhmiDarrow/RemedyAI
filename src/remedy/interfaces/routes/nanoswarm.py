"""REST API for Remedy Nano Swarm status and control."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel


class RouterClassifyRequest(BaseModel):
    message: str = ""
    use_local: bool = False  # opt-in; can block ~seconds if llama-server is busy


def register_nanoswarm_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    @app.get("/api/nanoswarm/status")
    async def nanoswarm_status() -> dict[str, Any]:
        from remedy.nanoswarm import get_swarm
        from remedy.runtime.bundle import bundle_available
        from remedy.runtime.catalog import catalog_public

        swarm = get_swarm().status()
        return {
            **swarm,
            "bundle": bundle_available(),
            "catalog": {
                "default_model_id": catalog_public().get("default_local_model_id"),
                "roles": catalog_public().get("roles"),
                "bundle_policy": catalog_public().get("bundle_policy"),
            },
        }

    @app.post("/api/nanoswarm/classify")
    async def nanoswarm_classify(body: RouterClassifyRequest) -> dict[str, Any]:
        """Classify intent. Default: fast heuristics. Set use_local for Qwen refine."""
        import asyncio

        from remedy.nanoswarm import get_swarm

        router = get_swarm().router
        if not body.use_local:
            return router.classify_intent(body.message or "")
        return await asyncio.to_thread(
            router.classify, body.message or "", use_local=True
        )

    @app.get("/api/nanoswarm/jobs")
    async def nanoswarm_jobs() -> dict[str, Any]:
        from remedy.runtime.jobs import default_queue

        return default_queue().status()
