"""Provider auth routes (xAI OAuth device-code + API key)."""
from __future__ import annotations

import contextlib
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Any used by connected-providers payload typing

from remedy.interfaces.api_support import (
    _apply_llm_to_runtime,
    _default_config_path,
    _find_config_path,
    _write_config,
    load_config,
)
from remedy.interfaces.config import (
    detect_ollama,
    normalize_llm_settings,
    public_provider_catalog,
)

logger = logging.getLogger(__name__)


class XaiApiKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=1)


def _home_from_config(cfg: dict[str, Any] | None = None):
    from pathlib import Path

    cfg = cfg if cfg is not None else load_config()
    home = cfg.get("home_dir")
    return Path(home).expanduser() if home else None


def _hot_reload_xai(runtime, cfg: dict[str, Any] | None = None) -> None:
    """Push xAI credentials into the live runtime when provider is xai."""
    if runtime is None:
        return
    cfg = cfg if cfg is not None else load_config()
    provider = str(cfg.get("llm_provider") or "").lower()
    if provider != "xai":
        return
    try:
        from remedy.interfaces.xai_auth import resolve_bearer

        token = resolve_bearer(_home_from_config(cfg))
    except Exception as exc:
        logger.debug("xAI hot-reload resolve failed: %s", exc)
        return
    if not token:
        return
    provider, model, base_url = normalize_llm_settings(
        cfg.get("llm_provider") or "xai",
        cfg.get("llm_model"),
        cfg.get("llm_base_url"),
    )
    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=token,
    )


def register_auth_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register provider auth endpoints."""

    @app.get("/api/providers")
    async def list_providers():
        """Known providers with base URL, models, and auth modes."""
        return {"providers": public_provider_catalog()}

    @app.get("/api/providers/connected")
    async def list_connected_providers():
        """Providers ready for the main-screen picker (connected + enabled).

        Connected = has credentials / OAuth / local detect / demo.
        Enabled = listed in config enabled_providers (default: all connected).
        """
        from remedy.interfaces.config import (
            PROVIDER_CATALOG,
            detect_ollama,
            get_provider_keys,
            public_provider_catalog,
        )
        from remedy.interfaces.secret_store import public_secret_status

        cfg = load_config()
        catalog = {p["id"]: p for p in public_provider_catalog()}
        keys = get_provider_keys(cfg)
        try:
            keys_set = public_secret_status(_home_from_config(cfg)).get(
                "provider_keys_set"
            ) or {}
        except Exception:
            keys_set = {k: True for k in keys}

        enabled_raw = cfg.get("enabled_providers")
        if isinstance(enabled_raw, str) and enabled_raw.strip():
            enabled_set = {
                x.strip().lower() for x in enabled_raw.split(",") if x.strip()
            }
        elif isinstance(enabled_raw, list) and enabled_raw:
            enabled_set = {str(x).strip().lower() for x in enabled_raw if str(x).strip()}
        else:
            enabled_set = None  # all connected are enabled

        enabled_models_cfg = cfg.get("enabled_models") or {}
        if not isinstance(enabled_models_cfg, dict):
            enabled_models_cfg = {}
        last_by = cfg.get("last_model_by_provider") or {}
        if not isinstance(last_by, dict):
            last_by = {}

        ollama = detect_ollama()
        xai_connected = False
        try:
            from remedy.interfaces.xai_auth import load_credentials

            xai_connected = bool(load_credentials(_home_from_config(cfg)).connected)
        except Exception:
            pass

        items: list[dict[str, Any]] = []
        for pid, meta in catalog.items():
            connected = False
            reason = "no_credentials"
            if pid == "demo":
                connected = True
                reason = "demo"
            elif pid == "ollama":
                connected = bool(ollama.get("available"))
                reason = "ollama_up" if connected else "ollama_down"
            elif pid == "xai" and xai_connected:
                connected = True
                reason = "oauth_or_key"
            elif keys.get(pid) or keys_set.get(pid):
                connected = True
                reason = "api_key"
            # custom with base_url may still need a key — treat key/env as connected

            enabled = True if enabled_set is None else (pid in enabled_set)
            models = list(meta.get("models") or [])
            allow = enabled_models_cfg.get(pid)
            if isinstance(allow, list) and allow:
                allow_set = {str(x) for x in allow}
                models = [m for m in models if str(m.get("id")) in allow_set]
            last_model = last_by.get(pid) or meta.get("default_model")
            if models and last_model and not any(
                str(m.get("id")) == str(last_model) for m in models
            ):
                last_model = models[0].get("id")
            items.append(
                {
                    **meta,
                    "connected": connected,
                    "connect_reason": reason,
                    "enabled": enabled,
                    "picker_eligible": bool(connected and enabled),
                    "models": models,
                    "last_model": last_model,
                    "catalog_models": list(
                        (PROVIDER_CATALOG.get(pid) or {}).get("models") or []
                    ),
                }
            )

        active = str(cfg.get("llm_provider") or "").lower()
        return {
            "providers": items,
            "connected": [p for p in items if p["connected"]],
            "picker": [p for p in items if p["picker_eligible"]],
            "active_provider": active,
            "active_model": cfg.get("llm_model"),
            "enabled_providers": (
                sorted(enabled_set) if enabled_set is not None else None
            ),
        }

    @app.get("/api/providers/free")
    async def list_free_providers():
        """Curated free / local / demo options for Setup and Settings."""
        from remedy.interfaces.config import free_options_public

        return {"options": free_options_public()}

    @app.get("/api/providers/ollama/detect")
    async def ollama_detect():
        """Probe local Ollama for first-run / setup suggestions."""
        return detect_ollama()

    @app.get("/api/auth/xai")
    async def xai_auth_status():
        from remedy.interfaces.xai_auth import load_credentials

        creds = load_credentials(_home_from_config())
        return creds.to_public_dict()

    @app.get("/api/auth/xai/oauth-meta")
    async def xai_oauth_meta():
        """Diagnostics: which OAuth host this server build uses."""
        from remedy.interfaces import xai_auth

        return {
            "oauth_build": getattr(xai_auth, "OAUTH_BUILD_ID", "unknown"),
            "device_code_url": getattr(xai_auth, "DEVICE_CODE_URL", ""),
            "token_url": getattr(xai_auth, "TOKEN_URL", ""),
            "accounts_server": getattr(xai_auth, "ACCOUNTS_SERVER", ""),
        }

    @app.post("/api/auth/xai/login")
    async def xai_auth_login():
        """Start OAuth device-code flow (Sign in with xAI)."""
        from remedy.interfaces.xai_auth import start_device_login

        try:
            result = start_device_login(home=_home_from_config())
        except Exception as exc:
            logger.warning("xAI device login failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"xAI OAuth start failed: {exc}") from exc
        return result

    @app.get("/api/auth/xai/login/status")
    async def xai_auth_login_status(session_id: str | None = None):
        from remedy.interfaces.xai_auth import login_status

        status = login_status(session_id=session_id, home=_home_from_config())
        # Once connected, ensure config + runtime use xAI if user is mid-setup.
        sess = (status.get("session") or {})
        if sess.get("status") == "connected":
            # User completed device OAuth — switch active provider to xAI so chat
            # uses the new credentials immediately (desktop Settings also PUTs).
            cfg = load_config()
            config_path = _find_config_path() or _default_config_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)
            provider, model, base_url = normalize_llm_settings(
                "xai",
                cfg.get("llm_model"),
                cfg.get("llm_base_url") or "https://api.x.ai/v1",
            )
            prev = str(cfg.get("llm_provider") or "").strip().lower()
            cfg.update(
                {
                    "llm_provider": provider,
                    "llm_model": model,
                    "llm_base_url": base_url,
                }
            )
            if prev and prev != "xai":
                cfg["last_llm_provider"] = prev
            _write_config(config_path, cfg)
            _hot_reload_xai(runtime, cfg)
        return status

    @app.post("/api/auth/xai/apikey")
    async def xai_auth_apikey(req: XaiApiKeyRequest):
        """Store an xAI console API key (secondary to OAuth)."""
        from remedy.interfaces.xai_auth import save_api_key

        try:
            creds = save_api_key(req.api_key, home=_home_from_config())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cfg = load_config()
        config_path = _find_config_path() or _default_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        provider, model, base_url = normalize_llm_settings(
            cfg.get("llm_provider") or "xai",
            cfg.get("llm_model"),
            cfg.get("llm_base_url") or "https://api.x.ai/v1",
        )
        # If user is on xAI (or has no provider), keep key in config too for CLI parity.
        if provider == "xai" or not cfg.get("llm_provider"):
            provider, model, base_url = normalize_llm_settings("xai", model, base_url)
            cfg["llm_provider"] = provider
            cfg["llm_model"] = model
            cfg["llm_base_url"] = base_url
            cfg["llm_api_key"] = req.api_key.strip()
            _write_config(config_path, cfg)
            _apply_llm_to_runtime(
                runtime,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=req.api_key.strip(),
            )
        return {"status": "saved", **creds.to_public_dict()}

    @app.delete("/api/auth/xai")
    async def xai_auth_logout():
        """Clear stored xAI OAuth tokens and API key."""
        from remedy.interfaces.xai_auth import clear_credentials

        clear_credentials(home=_home_from_config())
        cfg = load_config()
        if str(cfg.get("llm_provider") or "").lower() == "xai" and cfg.get("llm_api_key"):
            config_path = _find_config_path() or _default_config_path()
            if config_path.exists():
                cfg.pop("llm_api_key", None)
                _write_config(config_path, cfg)
        if runtime is not None and str(getattr(runtime, "_llm_provider", "")).lower() == "xai":
            with contextlib.suppress(Exception):
                runtime._llm_api_key = ""
        return {"status": "logged_out", "provider": "xai", "connected": False}
