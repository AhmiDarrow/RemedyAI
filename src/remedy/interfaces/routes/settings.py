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


def _normalize_tool_process(cfg: dict | None = None, raw: object = None) -> str:
    """off | medium | full — default off. Legacy show_tool_calls bool maps to full."""
    if raw is None and isinstance(cfg, dict):
        raw = cfg.get("tool_process")
        if raw is None and cfg.get("show_tool_calls") is True:
            return "full"
    s = str(raw or "off").strip().lower()
    if s in ("medium", "med"):
        return "medium"
    if s in ("full", "on", "true", "1", "yes"):
        return "full"
    if raw is True:
        return "full"
    return "off"


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

        # Env bootstrap (e.g. XAI_API_KEY → preselect xAI) for display only.
        try:
            from remedy.interfaces.config import apply_env_provider_bootstrap

            cfg = apply_env_provider_bootstrap(cfg)
        except Exception:
            pass

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

        from remedy.interfaces.config import (
            migrate_provider_keys,
            resolve_provider_api_key,
        )
        from remedy.interfaces.secret_store import public_secret_status

        # Migrate any leftover plaintext keys out of config into secure store.
        cfg_migrated = migrate_provider_keys(cfg)
        if cfg_migrated != cfg or cfg.get("provider_keys") or cfg.get("llm_api_key"):
            try:
                if config_path is not None:
                    _write_config(config_path, cfg_migrated)
            except Exception:
                pass
            cfg = cfg_migrated

        home_for_secrets = cfg.get("home_dir")
        from pathlib import Path as _Path

        home_path = _Path(home_for_secrets).expanduser() if home_for_secrets else None
        secret_status = public_secret_status(home_path)

        runtime_key = ""
        if runtime is not None:
            # Do not treat runtime key material as something we echo — only bool.
            runtime_key = str(getattr(runtime, "_llm_api_key", "") or "")
        effective_key = resolve_provider_api_key(cfg, provider, home=home_path)
        key_set = bool(effective_key or runtime_key)
        xai_auth: dict | None = None
        if provider == "xai":
            try:
                from remedy.interfaces.xai_auth import load_credentials

                creds = load_credentials(home_path)
                xai_auth = creds.to_public_dict()
                if creds.connected:
                    key_set = True
            except Exception:
                xai_auth = None

        out = {
            "llm_provider": provider,
            "llm_model": model,
            "llm_base_url": base_url,
            "llm_api_key_set": key_set,
            # Booleans only — never raw keys.
            "provider_keys_set": secret_status.get("provider_keys_set") or {},
            "secrets_encoding": secret_status.get("encoding"),
            "llm_ready": provider_credentials_ready(cfg) or bool(runtime_key) or bool(
                xai_auth and xai_auth.get("connected")
            ),
            "name": cfg.get("name", "Remedy"),
            "user_name": str(cfg.get("user_name") or "").strip(),
            "persona": cfg.get("persona", "default"),
            "project_path": cfg.get("project_path")
            or (
                str(runtime.effective_project_path())
                if runtime is not None and hasattr(runtime, "effective_project_path")
                else os.getcwd()
            ),
            "access_scope": cfg.get("access_scope", "project"),
            "launch_at_login": bool(cfg.get("launch_at_login", False)),
            "start_in_tray": bool(cfg.get("start_in_tray", False)),
            "close_to_tray": bool(cfg.get("close_to_tray", False)),
            "harness_mode": cfg.get("harness_mode", "auto"),
            "harness_min_context_pct": float(cfg.get("harness_min_context_pct", 0.35)),
            "harness_max_context_pct": float(cfg.get("harness_max_context_pct", 0.70)),
            "thinking_level": str(cfg.get("thinking_level") or "medium").lower(),
            "approval_mode": str(cfg.get("approval_mode") or "ask").lower(),
            "tool_process": _normalize_tool_process(cfg),
            "version": _remedy_version,
            "config_exists": config_path is not None,
            "setup_completed": setup_completed,
            "needs_setup": not setup_completed,
            "config_path": str(config_path) if config_path else str(_default_config_path()),
        }
        if xai_auth is not None:
            out["xai_auth"] = xai_auth
        return out

    @app.put("/api/settings")
    async def update_settings(req: SettingsUpdateRequest):
        config_path = _find_config_path()
        if config_path is None:
            config_path = _default_config_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)

        from pathlib import Path

        from remedy.interfaces.config import (
            migrate_provider_keys,
            resolve_provider_api_key,
            set_provider_key,
        )
        from remedy.interfaces.secret_store import scrub_config_secrets

        cfg = migrate_provider_keys(load_config())
        prev_provider = str(cfg.get("llm_provider") or "").strip().lower()
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
        if prev_provider and prev_provider != provider:
            updates["last_llm_provider"] = prev_provider

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

        if "access_scope" in updates and updates["access_scope"] is not None:
            from remedy.core.workspace import normalize_access_scope

            updates["access_scope"] = normalize_access_scope(str(updates["access_scope"]))

        if "harness_mode" in updates and updates["harness_mode"] is not None:
            hm = str(updates["harness_mode"]).strip().lower()
            updates["harness_mode"] = hm if hm in ("off", "manual", "auto") else "auto"

        if "thinking_level" in updates and updates["thinking_level"] is not None:
            tl = str(updates["thinking_level"]).strip().lower()
            updates["thinking_level"] = (
                tl if tl in ("off", "low", "medium", "high") else "medium"
            )

        if "approval_mode" in updates and updates["approval_mode"] is not None:
            am = str(updates["approval_mode"]).strip().lower()
            updates["approval_mode"] = am if am in ("ask", "auto") else "ask"
            try:
                from remedy.core.approvals import APPROVALS

                APPROVALS.set_mode(updates["approval_mode"])
            except Exception:
                pass

        # tool_process: off | medium | full (legacy show_tool_calls bool → full/off)
        if "tool_process" in updates and updates["tool_process"] is not None:
            updates["tool_process"] = _normalize_tool_process(raw=updates["tool_process"])
        elif "show_tool_calls" in updates and updates["show_tool_calls"] is not None:
            updates["tool_process"] = (
                "full" if updates["show_tool_calls"] else "off"
            )
        updates.pop("show_tool_calls", None)

        # Secrets go ONLY to the secure store — never into config.toml.
        incoming_key = updates.pop("llm_api_key", None)
        cfg.update(updates)
        home = cfg.get("home_dir")
        home_path = Path(home).expanduser() if home else None

        if incoming_key is not None and str(incoming_key).strip():
            set_provider_key(
                cfg, provider, str(incoming_key).strip(), home=home_path
            )
            if provider == "xai":
                try:
                    from remedy.interfaces.xai_auth import save_api_key

                    save_api_key(str(incoming_key).strip(), home=home_path)
                except Exception as exc:
                    logger.debug("xAI settings key sync: %s", exc)

        # Keep profile.display_name in sync so the agent addresses the user correctly.
        if "user_name" in updates and updates["user_name"] is not None:
            uname = str(updates["user_name"]).strip()
            updates["user_name"] = uname
            if memory is not None and uname:
                try:
                    profile = await memory.get_or_create_profile()
                    profile.display_name = uname
                    await memory.save_user_profile(profile)
                except Exception as exc:
                    logger.debug("sync user_name → profile: %s", exc)

        # Always scrub before disk write (no llm_api_key / provider_keys).
        cfg = scrub_config_secrets(cfg)
        cfg["llm_api_key"] = ""
        cfg.pop("provider_keys", None)
        _write_config(config_path, cfg)

        # Invalidate model discovery cache after provider/url changes.
        cache = getattr(app.state, "_model_discovery_cache", None)
        if isinstance(cache, dict):
            cache.clear()

        # Hot-reload live agent so the next chat uses the saved provider's own key.
        api_key_for_runtime = resolve_provider_api_key(cfg, provider)
        _apply_llm_to_runtime(
            runtime,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key_for_runtime or None,
            persona=updates.get("persona"),
            name=updates.get("name"),
            project_path=updates.get("project_path", cfg.get("project_path")),
            access_scope=cfg.get("access_scope"),
            harness_mode=cfg.get("harness_mode"),
            harness_min_context_pct=cfg.get("harness_min_context_pct"),
            harness_max_context_pct=cfg.get("harness_max_context_pct"),
            thinking_level=cfg.get("thinking_level"),
            approval_mode=cfg.get("approval_mode"),
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
            "access_scope": cfg.get("access_scope", "project"),
            "launch_at_login": bool(cfg.get("launch_at_login", False)),
            "start_in_tray": bool(cfg.get("start_in_tray", False)),
            "close_to_tray": bool(cfg.get("close_to_tray", False)),
            "harness_mode": cfg.get("harness_mode", "auto"),
            "thinking_level": str(cfg.get("thinking_level") or "medium"),
            "approval_mode": str(cfg.get("approval_mode") or "ask"),
            "user_name": str(cfg.get("user_name") or "").strip(),
            "tool_process": _normalize_tool_process(cfg),
        }

