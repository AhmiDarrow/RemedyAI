"""REST API for RMB — Remedy Muscle Bridge (local llama.cpp chat host)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from remedy.interfaces.api_support import load_config

logger = logging.getLogger(__name__)


class RmbSettingsPatch(BaseModel):
    enabled: bool | None = None
    auto_start: bool | None = None
    model_id: str | None = None
    model_path: str | None = None
    runtime_binary: str | None = None
    ctx_size: int | None = None
    n_gpu_layers: int | None = None
    profile: str | None = None
    flash_attn: bool | None = None
    port: int | None = None
    use_as_chat_provider: bool | None = Field(
        default=None,
        description="When true, set llm_provider=rmb and base_url to this host",
    )
    # --- inference-engine knobs (llama-server argv; all optional) ---
    threads: int | None = None
    parallel: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    repeat_penalty: float | None = None
    repeat_last_n: int | None = None
    seed: int | None = None
    batch_size: int | None = None
    ubatch_size: int | None = None
    mmproj: str | None = None
    chat_template: str | None = None
    use_jinja: bool | None = None
    rope_freq_scale: float | None = None
    rope_freq_base: float | None = None
    mlock: bool | None = None
    no_mmap: bool | None = None
    cache_type: str | None = None
    # --- KoboldCpp-class parity knobs (all optional; skipped when unset) ---
    typical_p: float | None = None
    tfs_z: float | None = None
    mirostat: int | None = None
    mirostat_tau: float | None = None
    mirostat_eta: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    main_gpu: int | None = None
    threads_batch: int | None = None
    tensor_split: str | None = None
    samplers: str | None = None
    rope_scaling: str | None = None
    yarn_orig_ctx: int | None = None
    yarn_factor: float | None = None
    yarn_beta_fast: float | None = None
    yarn_beta_slow: float | None = None
    no_kv_offload: bool | None = None
    # --- DRY + XTC samplers (KoboldCpp parity) ---
    dry_multiplier: float | None = None
    dry_base: float | None = None
    dry_allowed_length: int | None = None
    dry_penalty_last_n: int | None = None
    xtc_probability: float | None = None
    xtc_threshold: float | None = None


class RmbHfQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class RmbHfFilesQuery(BaseModel):
    repo: str = Field(..., min_length=3, max_length=200)
    revision: str | None = None


class RmbHfPull(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    repo: str | None = Field(default=None, max_length=200)
    filename: str | None = Field(default=None, max_length=300)
    revision: str | None = None
    url: str | None = Field(default=None, max_length=800)
    expected_size: int | None = None
    load: bool = True


def register_rmb_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register /api/rmb/* routes."""

    @app.get("/api/rmb/status")
    async def rmb_status() -> dict[str, Any]:
        import asyncio

        from remedy.runtime.rmb.service import get_rmb_status

        cfg = load_config()
        return await asyncio.to_thread(get_rmb_status, cfg)

    @app.get("/api/rmb/catalog")
    async def rmb_catalog() -> dict[str, Any]:
        from remedy.runtime.rmb.catalog import catalog_public

        return catalog_public()

    @app.post("/api/rmb/start")
    async def rmb_start() -> dict[str, Any]:
        import asyncio

        from remedy.runtime.rmb.service import apply_rmb_settings, start_rmb_server

        cfg = load_config()
        home = cfg.get("home_dir") if isinstance(cfg, dict) else None
        # ensure enabled
        await asyncio.to_thread(
            apply_rmb_settings, {"enabled": True}, home_dir=home, cfg=cfg
        )
        return await asyncio.to_thread(start_rmb_server, home_dir=home, wait_s=120.0)

    @app.post("/api/rmb/stop")
    async def rmb_stop() -> dict[str, Any]:
        import asyncio

        from remedy.runtime.rmb.service import stop_rmb_server

        cfg = load_config()
        home = cfg.get("home_dir") if isinstance(cfg, dict) else None
        return await asyncio.to_thread(stop_rmb_server, home_dir=home)

    @app.post("/api/rmb/settings")
    async def rmb_settings(body: RmbSettingsPatch) -> dict[str, Any]:
        """Save RMB settings and apply them to the *live* process when needed.

        ctx_size / model / GPU layers / port require a llama-server restart —
        disk-only saves left the old n_ctx running (e.g. still 8192 after 32768).
        """
        import asyncio

        from remedy.interfaces.api_support import _apply_llm_to_runtime
        from remedy.interfaces.api_support import load_config as _lc
        from remedy.runtime.rmb.catalog import DEFAULT_RMB_MODEL_ID, get_model_spec
        from remedy.runtime.rmb.config import load_rmb_json, merge_state
        from remedy.runtime.rmb.service import apply_rmb_settings

        cfg = load_config()
        home = cfg.get("home_dir") if isinstance(cfg, dict) else None
        patch = body.model_dump(exclude_none=True)
        # Settings that affect the running server — always live-apply (restart)
        result = await asyncio.to_thread(
            apply_rmb_settings,
            patch,
            home_dir=home,
            cfg=cfg,
            live=True,
            wait_s=120.0,
        )
        # Hot-apply chat binding when provider is already RMB (or use_as_chat).
        # Always use GGUF stem so Provider / status bar match Loaded GGUF.
        try:
            disk = _lc()
            rstate = merge_state(load_rmb_json(home))
            prov = str(
                (disk or {}).get("llm_provider")
                if isinstance(disk, dict)
                else ""
            ).lower()
            want_rmb = prov == "rmb" or bool(patch.get("use_as_chat_provider"))
            model = ""
            if rstate.get("model_path"):
                from pathlib import Path as _P

                model = _P(str(rstate["model_path"])).stem
            if not model:
                mid = str(rstate.get("model_id") or DEFAULT_RMB_MODEL_ID)
                from remedy.runtime.rmb.catalog import RMB_MODELS

                if mid in RMB_MODELS:
                    model = get_model_spec(mid).filename.replace(".gguf", "")
                else:
                    model = mid
            result["chat_model"] = model
            result["llm_model"] = model
            if want_rmb and runtime is not None and model:
                base = str(
                    rstate.get("base_url") or "http://127.0.0.1:8787/v1"
                )
                _apply_llm_to_runtime(
                    runtime,
                    provider="rmb",
                    model=model,
                    base_url=base,
                    api_key="rmb",
                    harness_mode="auto",
                    harness_min_context_pct=0.55,
                    harness_max_context_pct=0.78,
                )
                result["runtime_applied"] = True
            else:
                result["runtime_applied"] = False
        except Exception:
            logger.exception("RMB settings live runtime reconfigure failed")
            result["runtime_applied"] = False
        return result

    @app.post("/api/rmb/use")
    async def rmb_use_as_provider() -> dict[str, Any]:
        """Enable RMB, start server, switch chat provider to rmb (live runtime too)."""
        import asyncio

        from remedy.interfaces.api_support import _apply_llm_to_runtime
        from remedy.runtime.rmb.catalog import DEFAULT_RMB_MODEL_ID, get_model_spec
        from remedy.runtime.rmb.config import load_rmb_json, merge_state
        from remedy.runtime.rmb.service import (
            apply_rmb_settings,
            start_rmb_server,
        )

        cfg = load_config()
        home = cfg.get("home_dir") if isinstance(cfg, dict) else None
        st = await asyncio.to_thread(
            apply_rmb_settings,
            {"enabled": True, "auto_start": True, "use_as_chat_provider": True},
            home_dir=home,
            cfg=cfg,
        )
        start = await asyncio.to_thread(start_rmb_server, home_dir=home, wait_s=120.0)
        # Hot-apply so the next message uses RMB without restart
        try:
            from pathlib import Path as _P

            from remedy.runtime.rmb.catalog import RMB_MODELS

            rstate = merge_state(load_rmb_json(home))
            base = str(rstate.get("base_url") or "http://127.0.0.1:8787/v1")
            model = ""
            if rstate.get("model_path"):
                model = _P(str(rstate["model_path"])).stem
            if not model:
                mid = str(rstate.get("model_id") or DEFAULT_RMB_MODEL_ID)
                if mid in RMB_MODELS:
                    model = get_model_spec(mid).filename.replace(".gguf", "")
                else:
                    model = mid
            disk = load_config()
            api_key = "rmb"
            if isinstance(disk, dict) and disk.get("llm_api_key"):
                api_key = str(disk.get("llm_api_key") or "rmb")
            if runtime is not None and model:
                _apply_llm_to_runtime(
                    runtime,
                    provider="rmb",
                    model=model,
                    base_url=base,
                    api_key=api_key,
                    harness_mode="auto",
                    harness_min_context_pct=0.55,
                    harness_max_context_pct=0.78,
                )
        except Exception:
            logger.exception("RMB use-as-provider live reconfigure failed")
        return {"status": st, "start": start, "runtime_applied": runtime is not None}

    @app.post("/api/rmb/hf/search")
    async def rmb_hf_search(body: RmbHfQuery) -> dict[str, Any]:
        """Resolve a name / repo / Hugging Face URL into the next choice list."""
        import asyncio

        from remedy.runtime.rmb.hf import HfError, resolve_query

        try:
            return await asyncio.to_thread(resolve_query, body.query)
        except HfError as exc:
            return {"ok": False, "error": str(exc), "repos": [], "files": []}

    @app.post("/api/rmb/hf/files")
    async def rmb_hf_files(body: RmbHfFilesQuery) -> dict[str, Any]:
        """List .gguf files in a Hugging Face model repo."""
        import asyncio

        from remedy.runtime.rmb.hf import HfError, list_gguf_files, sanitize_repo

        try:
            repo = sanitize_repo(body.repo)
            files = await asyncio.to_thread(
                list_gguf_files, repo, revision=body.revision
            )
            return {"ok": True, "repo": repo, "files": files}
        except HfError as exc:
            return {"ok": False, "error": str(exc), "files": []}

    @app.post("/api/rmb/hf/pull")
    async def rmb_hf_pull(body: RmbHfPull) -> dict[str, Any]:
        """Download a GGUF into ~/.remedy/rmb/models/ (background)."""
        import asyncio

        from remedy.runtime.rmb.hf import HfError, parse_hf_hint, start_pull

        cfg = load_config()
        home = cfg.get("home_dir") if isinstance(cfg, dict) else None
        repo = body.repo
        filename = body.filename
        revision = body.revision
        url = body.url
        raw = (body.query or url or "").strip()
        if raw and (not repo or not filename):
            try:
                hint = parse_hf_hint(raw)
            except HfError as exc:
                return {"ok": False, "error": str(exc)}
            repo = repo or hint.repo
            filename = filename or hint.filename
            revision = revision or hint.revision
            url = url or hint.url
        try:
            return await asyncio.to_thread(
                start_pull,
                repo=repo,
                filename=filename,
                revision=revision,
                url=url,
                home_dir=home,
                expected_size=int(body.expected_size or 0),
                load=bool(body.load),
            )
        except HfError as exc:
            return {"ok": False, "error": str(exc)}

    @app.get("/api/rmb/hf/progress")
    async def rmb_hf_progress() -> dict[str, Any]:
        from remedy.runtime.rmb.hf import progress_snapshot

        return {"ok": True, "progress": progress_snapshot()}

    @app.post("/api/rmb/hf/cancel")
    async def rmb_hf_cancel() -> dict[str, Any]:
        from remedy.runtime.rmb.hf import cancel_pull

        return cancel_pull()
