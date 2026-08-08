"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from remedy import __version__ as _remedy_version
from remedy.interfaces.api_models import (
    SettingsUpdateRequest,
)
from remedy.interfaces.api_support import (
    _default_config_path,
    _find_config_path,
    load_config,
)
from remedy.interfaces.config import (
    needs_first_run_setup,
    normalize_llm_settings,
    provider_credentials_ready,
)

logger = logging.getLogger(__name__)


def _normalize_tool_process(cfg: dict | None = None, raw: object = None) -> str:
    """off | medium | full — default off. Legacy show_tool_calls / full+ → full."""
    if raw is None and isinstance(cfg, dict):
        raw = cfg.get("tool_process")
        if raw is None and cfg.get("show_tool_calls") is True:
            return "full"
    s = str(raw or "off").strip().lower()
    if s in ("medium", "med"):
        return "medium"
    # full+ removed from product UI — collapse to full
    if s in ("full", "full+", "fullplus", "full_plus", "debug", "on", "true", "1", "yes"):
        return "full"
    if raw is True:
        return "full"
    return "off"


def _effective_http_bootstrap(cfg: dict | None = None) -> bool:
    """Whether loopback HTTP token bootstrap is on (env > config > default True)."""
    try:
        from remedy.interfaces.local_auth import http_bootstrap_enabled

        return bool(http_bootstrap_enabled())
    except Exception:
        if isinstance(cfg, dict) and "http_bootstrap" in cfg:
            return bool(cfg.get("http_bootstrap"))
        return True


DEFAULT_BROWSER_HOME_URL = "https://github.com/AhmiDarrow/RemedyAI"


def _normalize_browser_home_url(raw: object | None) -> str:
    """http(s) homepage for the in-app Browser slide; empty/invalid → Remedy GitHub."""
    u = str(raw or "").strip()
    if not u:
        return DEFAULT_BROWSER_HOME_URL
    low = u.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return DEFAULT_BROWSER_HOME_URL
    if not (low.startswith("http://") or low.startswith("https://")):
        if low.startswith("about:"):
            return DEFAULT_BROWSER_HOME_URL
        u = f"https://{u}"
    return u


def register_settings_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- settings -----------------------------------------------------------
    @app.get("/api/settings")
    async def get_settings():
        import time as _time

        t0 = _time.perf_counter()
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

        # Migrate plaintext keys into the secure store **in memory only** for this
        # response. Never rewrite config.toml from GET — concurrent PUT can lose
        # fields if a slow GET migrates an older tree after a newer save.
        needs_migrate = bool(cfg.get("provider_keys") or str(cfg.get("llm_api_key") or "").strip())
        if needs_migrate:
            cfg = migrate_provider_keys(cfg)

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
                xai_auth = creds.to_public_dict(home=home_path)
                if creds.connected:
                    key_set = True
            except Exception:
                xai_auth = None

        out = {
            "llm_provider": provider,
            "llm_model": model,
            "llm_base_url": base_url,
            "custom_llm_name": str(cfg.get("custom_llm_name") or "").strip(),
            "llm_api_key_set": key_set,
            # Booleans only — never raw keys.
            "provider_keys_set": secret_status.get("provider_keys_set") or {},
            "secrets_encoding": secret_status.get("encoding"),
            "secrets_encoding_warning": secret_status.get("encoding_warning"),
            "llm_ready": provider_credentials_ready(cfg) or bool(runtime_key) or bool(
                xai_auth and xai_auth.get("connected")
            ),
            "name": cfg.get("name", "Remedy"),
            "user_name": str(cfg.get("user_name") or "").strip(),
            "agent_gender": (
                g
                if (
                    g := str(cfg.get("agent_gender") or "female").strip().lower()
                )
                in ("female", "male", "neutral")
                else "female"
            ),
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
            "harness_min_context_pct": float(cfg.get("harness_min_context_pct", 0.75)),
            "harness_max_context_pct": float(cfg.get("harness_max_context_pct", 0.92)),
            "thinking_level": str(cfg.get("thinking_level") or "high").lower(),
            "approval_mode": str(cfg.get("approval_mode") or "ask").lower(),
            "tool_process": _normalize_tool_process(cfg),
            "web_tools_enabled": bool(cfg.get("web_tools_enabled", False)),
            "http_bootstrap": _effective_http_bootstrap(cfg),
            # Opt-in: tighter tool caps + email/phone scrub on LLM egress (default off = fast)
            "privacy_mode": bool(cfg.get("privacy_mode", False)),
            # Align with feature_maturity defaults (soul on unless explicitly off).
            "soul_field_enabled": (
                bool(cfg["soul_field_enabled"])
                if "soul_field_enabled" in cfg
                else True
            ),
            "build_os_advanced": bool(cfg.get("build_os_advanced", False)),
            "rmb_enabled": bool(cfg.get("rmb_enabled", True)),
            "retention_session_days": int(cfg.get("retention_session_days") or 0),
            "retention_attachment_days": int(cfg.get("retention_attachment_days") or 0),
            "retention_computer_shot_days": int(
                cfg.get("retention_computer_shot_days")
                if cfg.get("retention_computer_shot_days") is not None
                else 14
            ),
            "retention_undo_days": int(
                cfg.get("retention_undo_days")
                if cfg.get("retention_undo_days") is not None
                else 30
            ),
            "retention_log_days": int(
                cfg.get("retention_log_days")
                if cfg.get("retention_log_days") is not None
                else 30
            ),
            "memory_encrypt": bool(cfg.get("memory_encrypt", False)),
            "allow_skill_creation": bool(cfg.get("allow_skill_creation", True)),
            "auto_approve_threshold": float(cfg.get("auto_approve_threshold", 0.8)),
            "log_level": str(cfg.get("log_level") or "INFO").upper(),
            "sarcasm_mode": bool(cfg.get("sarcasm_mode", False)),
            "enabled_providers": cfg.get("enabled_providers"),
            "enabled_models": cfg.get("enabled_models") or {},
            "last_model_by_provider": cfg.get("last_model_by_provider") or {},
            "skills_active_budget": int(cfg.get("skills_active_budget") or 80),
            "browser_home_url": _normalize_browser_home_url(cfg.get("browser_home_url")),
            "version": _remedy_version,
            "config_exists": config_path is not None,
            "setup_completed": setup_completed,
            "needs_setup": not setup_completed,
            "config_path": str(config_path) if config_path else str(_default_config_path()),
        }
        try:
            from remedy.interfaces.messenger_settings import messengers_for_settings_response

            out["enabled_channels"], out["messengers"] = messengers_for_settings_response(
                cfg, home_path
            )
        except Exception as exc:
            logger.debug("settings messengers: %s", exc)
            out["enabled_channels"] = ["cli"]
            out["messengers"] = []
        # Personal assistant (local store + planned OAuth providers)
        # Read-only on GET: never write the assistant store from a settings fetch
        # (stale config.toml must not clobber store-only prefs).
        try:
            from remedy.assistant.store import get_assistant_store

            astore = get_assistant_store(home_path)
            pub = astore.public_status()
            # Display-only overlay of config.toml flags (does not persist).
            acfg = cfg.get("assistant") if isinstance(cfg.get("assistant"), dict) else {}
            if acfg:
                if "enabled" in acfg:
                    pub["enabled"] = bool(acfg["enabled"])
                if "timezone" in acfg and acfg["timezone"] is not None:
                    pub["timezone"] = str(acfg["timezone"])
                if "money_disclaimer_accepted" in acfg:
                    pub["money_disclaimer_accepted"] = bool(
                        acfg["money_disclaimer_accepted"]
                    )
            out["assistant"] = pub
        except Exception as exc:
            logger.debug("settings assistant: %s", exc)
            out["assistant"] = {
                "enabled": True,
                "accounts": [],
                "providers_planned": [
                    {"id": "google", "name": "Google", "status": "planned"},
                    {"id": "microsoft", "name": "Microsoft", "status": "planned"},
                    {"id": "yahoo", "name": "Yahoo", "status": "planned"},
                ],
            }
        # Visual decoder summary only (full detail via /api/vision/status).
        # Always use light=True — full get_status used to block the event loop
        # for seconds when llama-server was down, freezing /api/status polls.
        try:
            from remedy.vision.config import vision_section_from_config
            from remedy.vision.service import get_status as vision_get_status

            vsec = vision_section_from_config(cfg)
            out["vision_enabled"] = bool(vsec.get("enabled"))
            from remedy.vision.catalog import DEFAULT_MODEL_ID

            out["vision_model_id"] = str(vsec.get("model_id") or DEFAULT_MODEL_ID)
            out["vision_force_decode"] = bool(vsec.get("force_decode"))
            vst = vision_get_status(cfg, light=True)
            out["vision"] = {
                "enabled": vst.get("enabled"),
                "installed": vst.get("installed"),
                "ready": vst.get("ready"),
                "running": vst.get("running"),
                "model_id": vst.get("model_id"),
                "model_name": (vst.get("model") or {}).get("name"),
                "force_decode": bool(vsec.get("force_decode")),
            }
        except Exception as exc:
            logger.debug("settings vision summary failed: %s", exc)
            from remedy.vision.catalog import DEFAULT_MODEL_ID

            out["vision_enabled"] = False
            out["vision_model_id"] = DEFAULT_MODEL_ID
            out["vision_force_decode"] = False
        if xai_auth is not None:
            out["xai_auth"] = xai_auth
        ms = (_time.perf_counter() - t0) * 1000
        if ms >= 250:
            logger.warning("GET /api/settings slow (%.0fms) provider=%s", ms, provider)
        else:
            logger.debug("GET /api/settings (%.0fms) provider=%s", ms, provider)
        return out

    @app.put("/api/settings")
    async def update_settings(req: SettingsUpdateRequest):
        from fastapi import HTTPException

        from remedy.interfaces.settings_apply import apply_settings_update

        updates = req.model_dump(exclude_none=True)
        cache = getattr(app.state, "_model_discovery_cache", None)
        try:
            result = await apply_settings_update(
                updates,
                runtime=runtime,
                gateway=gateway,
                memory=memory,
                model_discovery_cache=cache if isinstance(cache, dict) else None,
            )
        except ValueError as exc:
            # Empty body is a no-op success (Settings Save sometimes sends {})
            if not updates:
                return {
                    "status": "saved",
                    "changes": [],
                    "config_path": str(_find_config_path() or _default_config_path()),
                }
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            logger.exception("Failed to write settings")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write config.toml: {exc}",
            ) from exc
        except Exception as exc:
            logger.exception("Settings save failed")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save settings: {exc}",
            ) from exc

        # Keep response shape compatible with desktop SettingsPanel
        return {
            "status": result.get("status", "saved"),
            "changes": result.get("changes") or [],
            "config_path": result.get("config_path", ""),
            "llm_provider": result.get("llm_provider"),
            "llm_model": result.get("llm_model"),
            "llm_base_url": result.get("llm_base_url"),
            "persona": result.get("persona"),
            "project_path": result.get("project_path"),
            "access_scope": result.get("access_scope", "project"),
            "launch_at_login": bool(result.get("launch_at_login", False)),
            "start_in_tray": bool(result.get("start_in_tray", False)),
            "close_to_tray": bool(result.get("close_to_tray", False)),
            "harness_mode": result.get("harness_mode", "auto"),
            "thinking_level": str(result.get("thinking_level") or "high"),
            "approval_mode": str(result.get("approval_mode") or "ask"),
            "user_name": str(result.get("user_name") or "").strip(),
            "tool_process": result.get("tool_process") or "off",
            "vision_enabled": bool(result.get("vision_enabled")),
            "vision_model_id": result.get("vision_model_id") or "",
            "vision_force_decode": bool(result.get("vision_force_decode")),
            "name": result.get("name"),
            "web_tools_enabled": bool(result.get("web_tools_enabled")),
            "http_bootstrap": bool(result.get("http_bootstrap", True)),
            "custom_llm_name": str(result.get("custom_llm_name") or "").strip(),
        }

