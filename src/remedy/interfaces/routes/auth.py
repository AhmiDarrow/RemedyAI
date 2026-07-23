"""Provider auth routes (xAI OAuth device-code + API key)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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

    @app.get("/api/providers/ollama/detect")
    async def ollama_detect():
        """Probe local Ollama for first-run / setup suggestions."""
        return detect_ollama()

    @app.get("/api/auth/xai")
    async def xai_auth_status():
        from remedy.interfaces.xai_auth import load_credentials

        creds = load_credentials(_home_from_config())
        return creds.to_public_dict()

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
            cfg = load_config()
            # Prefer not forcing provider switch unless already xai or unset.
            current = str(cfg.get("llm_provider") or "").lower()
            if current in ("", "xai"):
                config_path = _find_config_path() or _default_config_path()
                config_path.parent.mkdir(parents=True, exist_ok=True)
                provider, model, base_url = normalize_llm_settings(
                    "xai",
                    cfg.get("llm_model"),
                    cfg.get("llm_base_url"),
                )
                cfg.update(
                    {
                        "llm_provider": provider,
                        "llm_model": model,
                        "llm_base_url": base_url,
                    }
                )
                _write_config(config_path, cfg)
                _hot_reload_xai(runtime, cfg)
            else:
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
            try:
                runtime._llm_api_key = ""
            except Exception:
                pass
        return {"status": "logged_out", "provider": "xai", "connected": False}
