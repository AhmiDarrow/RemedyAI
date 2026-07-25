"""REST API for Remedy Nano Swarm status and control."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel


class RouterClassifyRequest(BaseModel):
    message: str = ""
    use_local: bool = False  # opt-in; can block ~seconds if llama-server is busy


class HelperHelpRequest(BaseModel):
    prompt: str = ""


class HelperErrorRequest(BaseModel):
    error: str = ""


class GuardAssessRequest(BaseModel):
    tool_name: str = ""
    command: str = ""
    path: str = ""


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

    @app.post("/api/nanoswarm/helper/help")
    async def nanoswarm_helper_help(body: HelperHelpRequest) -> dict[str, Any]:
        """Offline help cards (Helper nanobot)."""
        from remedy.nanoswarm import get_swarm

        return get_swarm().helper.draft_help(body.prompt or "")

    @app.post("/api/nanoswarm/helper/explain")
    async def nanoswarm_helper_explain(body: HelperErrorRequest) -> dict[str, Any]:
        """Offline explain-last-error (Helper nanobot)."""
        from remedy.nanoswarm import get_swarm

        return get_swarm().helper.explain_error(body.error or "")

    @app.post("/api/nanoswarm/guard/assess")
    async def nanoswarm_guard_assess(body: GuardAssessRequest) -> dict[str, Any]:
        from remedy.nanoswarm import get_swarm

        return get_swarm().guard.assess(
            tool_name=body.tool_name,
            command=body.command,
            path=body.path,
        )

    @app.get("/api/nanoswarm/token/families")
    async def nanoswarm_token_families() -> dict[str, Any]:
        """List NanoToken encoding-family weight packs (offline tables)."""
        from remedy.nanoswarm.token_tables import list_families

        return {"families": list_families()}

    @app.get("/api/nanoswarm/token/assignment")
    async def nanoswarm_token_assignment(
        provider: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        """Which Remedy BPE pack the swarm assigns for provider/model."""
        from remedy.nanoswarm.token_nanobot import resolve_bpe_assignment

        return resolve_bpe_assignment(provider or None, model or None)

    @app.get("/api/nanoswarm/token/packs")
    async def nanoswarm_token_packs() -> dict[str, Any]:
        """List Remedy-owned BPE packs (packaged + user overrides)."""
        from remedy.nanoswarm.bpe_engine import list_available_packs

        return {
            "packs": list_available_packs(),
            "ip_note": "Remedy-owned packs only; no third-party tokenizers.",
        }
