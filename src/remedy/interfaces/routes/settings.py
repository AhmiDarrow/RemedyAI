"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from remedy import __version__ as _remedy_version
from remedy.core.errors import SecurityError
from remedy.core.security import safe_path
from remedy.interfaces.api_models import (
    AttachmentRef,
    AttachmentUploadRequest,
    ChatRequest,
    ChatResponse,
    CommandRequest,
    CreateSessionRequest,
    MemoryAddRequest,
    MemorySearchRequest,
    SendMessageRequest,
    SettingsUpdateRequest,
    SkillInfo,
    StatusResponse,
    UpdateSessionRequest,
    WebhookPayload,
)
from remedy.interfaces.api_support import (
    _apply_llm_to_runtime,
    _BUILTIN_AGENTS,
    _BUILTIN_COMMANDS,
    _BUILTIN_MODELS,
    _default_config_path,
    _find_config_path,
    _load_config_cached,
    _serialize_toml,
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    _write_config,
    handle_slash_command,
    load_config,
    sse_headers,
)
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    catalog_models_for_provider,
    needs_first_run_setup,
    normalize_llm_settings,
    provider_credentials_ready,
)
from remedy.interfaces.config import _is_local_url
from remedy.models import (
    ChannelKind,
    ChatMessageRole,
    EventKind,
    GatewayEvent,
    MemoryEntryType,
)

logger = logging.getLogger(__name__)


def register_settings_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- settings -----------------------------------------------------------
    @app.get("/api/settings")
    async def get_settings():
        cfg = load_config()
        config_path = _find_config_path()
        # First-run: show wizard when needs_first_run_setup says so.
        # setup_completed True (including Skip) → never re-show automatically.
        setup_completed = not needs_first_run_setup(cfg, config_path=config_path)

        # Return configured values; soft-normalize only for response display.
        # Never write disk from GET (avoids races with PUT and Ollama false-heals).
        raw_provider = cfg.get("llm_provider", os.environ.get("REMEDY_LLM_PROVIDER", "openai"))
        raw_model = cfg.get("llm_model", os.environ.get("REMEDY_LLM_MODEL", "gpt-4o-mini"))
        raw_url = cfg.get("llm_base_url", os.environ.get("REMEDY_LLM_BASE_URL", "https://api.openai.com/v1"))
        provider, model, base_url = normalize_llm_settings(raw_provider, raw_model, raw_url)
        # Preserve flexible-provider models (ollama/custom/openrouter) as stored.
        if str(raw_provider or "").lower() in ("ollama", "custom", "openrouter"):
            provider = str(raw_provider or provider).lower()
            if raw_model:
                model = str(raw_model)
            if raw_url:
                base_url = str(raw_url)

        runtime_key = ""
        if runtime is not None:
            runtime_key = str(getattr(runtime, "_llm_api_key", "") or "")
        key_set = bool(
            cfg.get("llm_api_key") or os.environ.get("REMEDY_LLM_API_KEY") or runtime_key
        )

        return {
            "llm_provider": provider,
            "llm_model": model,
            "llm_base_url": base_url,
            "llm_api_key_set": key_set,
            "llm_ready": provider_credentials_ready(cfg) or bool(runtime_key),
            "name": cfg.get("name", "Remedy"),
            "persona": cfg.get("persona", "default"),
            "project_path": cfg.get("project_path")
            or (
                str(runtime.effective_project_path())
                if runtime is not None and hasattr(runtime, "effective_project_path")
                else os.getcwd()
            ),
            "version": version,
            "config_exists": config_path is not None,
            "setup_completed": setup_completed,
            "needs_setup": not setup_completed,
            "config_path": str(config_path) if config_path else str(_default_config_path()),
        }

    @app.put("/api/settings")
    async def update_settings(req: SettingsUpdateRequest):
        config_path = _find_config_path()
        if config_path is None:
            config_path = _default_config_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)

        cfg = load_config()
        updates = req.model_dump(exclude_none=True)

        if "llm_api_key" in updates and not updates["llm_api_key"]:
            del updates["llm_api_key"]

        # Merge then normalize provider/model/url so cross-provider combos
        # (e.g. deepseek + claude-3-haiku) cannot be persisted.
        merged = {**cfg, **updates}
        provider, model, base_url = normalize_llm_settings(
            merged.get("llm_provider"),
            merged.get("llm_model"),
            merged.get("llm_base_url"),
        )
        updates["llm_provider"] = provider
        updates["llm_model"] = model
        updates["llm_base_url"] = base_url

        # Normalize project_path to an absolute directory when provided.
        if "project_path" in updates and updates["project_path"] is not None:
            from remedy.core.workspace import ensure_project_dir, resolve_project_path

            raw_pp = str(updates["project_path"]).strip()
            if raw_pp and raw_pp not in (".", "./"):
                try:
                    updates["project_path"] = str(
                        ensure_project_dir(resolve_project_path(raw_pp))
                    )
                except Exception:
                    updates["project_path"] = str(resolve_project_path(raw_pp))
            else:
                # Explicit clear → store empty so sessions fall back to cwd
                updates["project_path"] = ""

        cfg.update(updates)
        _write_config(config_path, cfg)

        # Invalidate model discovery cache after provider/url changes.
        cache = getattr(app.state, "_model_discovery_cache", None)
        if isinstance(cache, dict):
            cache.clear()

        # Hot-reload live agent so the next chat uses the new endpoint/model/persona/project.
        api_key_for_runtime = updates.get("llm_api_key")
        _apply_llm_to_runtime(
            runtime,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key_for_runtime,
            persona=updates.get("persona"),
            name=updates.get("name"),
            project_path=updates.get("project_path", cfg.get("project_path")),
        )

        changes = list(updates.keys())
        return {
            "status": "saved",
            "changes": changes,
            "config_path": str(config_path),
            "llm_provider": provider,
            "llm_model": model,
            "llm_base_url": base_url,
            "persona": cfg.get("persona"),
            "project_path": cfg.get("project_path"),
        }

