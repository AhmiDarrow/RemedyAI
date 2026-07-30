"""Shared settings apply path — API PUT and agent ``update_settings`` tool.

Persists non-secret prefs to config.toml, secrets to the secure store, hot-reloads
the live runtime, and optionally messenger adapters.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from remedy.interfaces.api_support import (
    _apply_llm_to_runtime,
    _default_config_path,
    _find_config_path,
    _write_config,
    load_config,
)
from remedy.interfaces.config import normalize_llm_settings

logger = logging.getLogger(__name__)

DEFAULT_BROWSER_HOME_URL = "https://github.com/AhmiDarrow/RemedyAI"

# Flat keys the agent may set (mirrors SettingsUpdateRequest; no nested bags here).
SETTABLE_KEYS = frozenset(
    {
        "llm_provider",
        "llm_model",
        "llm_base_url",
        "llm_api_key",
        "project_path",
        "name",
        "user_name",
        "persona",
        "setup_completed",
        "access_scope",
        "launch_at_login",
        "start_in_tray",
        "close_to_tray",
        "harness_mode",
        "harness_min_context_pct",
        "harness_max_context_pct",
        "thinking_level",
        "approval_mode",
        "show_tool_calls",
        "tool_process",
        "vision_enabled",
        "vision_model_id",
        "vision_force_decode",
        "web_tools_enabled",
        "http_bootstrap",
        "allow_skill_creation",
        "auto_approve_threshold",
        "log_level",
        "sarcasm_mode",
        "enabled_providers",
        "enabled_models",
        "last_model_by_provider",
        "skills_active_budget",
        "browser_home_url",
        "enabled_channels",
        "messengers",
        "assistant",
    }
)


def normalize_tool_process(cfg: dict | None = None, raw: object = None) -> str:
    """off | medium | full — default off. Legacy show_tool_calls / full+ → full."""
    if raw is None and isinstance(cfg, dict):
        raw = cfg.get("tool_process")
        if raw is None and cfg.get("show_tool_calls") is True:
            return "full"
    s = str(raw or "off").strip().lower()
    if s in ("medium", "med"):
        return "medium"
    if s in ("full", "full+", "fullplus", "full_plus", "debug", "on", "true", "1", "yes"):
        return "full"
    if raw is True:
        return "full"
    return "off"


def normalize_browser_home_url(raw: object | None) -> str:
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


def public_settings_snapshot(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Safe settings view for tools (no secrets)."""
    from remedy.interfaces.config import (
        migrate_provider_keys,
        provider_credentials_ready,
        resolve_provider_api_key,
    )

    raw = cfg if isinstance(cfg, dict) else (load_config() or {})
    raw = migrate_provider_keys(raw if isinstance(raw, dict) else {})
    provider, model, base_url = normalize_llm_settings(
        raw.get("llm_provider"),
        raw.get("llm_model"),
        raw.get("llm_base_url"),
    )
    if str(raw.get("llm_provider") or "").lower() in ("ollama", "custom", "openrouter"):
        provider = str(raw.get("llm_provider") or provider).lower()
        if raw.get("llm_model"):
            model = str(raw["llm_model"])
        if raw.get("llm_base_url"):
            base_url = str(raw["llm_base_url"])
    home = raw.get("home_dir")
    home_path = Path(home).expanduser() if home else None
    key = resolve_provider_api_key(raw, provider, home=home_path)
    vision = raw.get("vision") if isinstance(raw.get("vision"), dict) else {}
    from remedy.vision.catalog import DEFAULT_MODEL_ID

    return {
        "llm_provider": provider,
        "llm_model": model,
        "llm_base_url": base_url,
        "llm_api_key_set": bool(key),
        "llm_ready": bool(provider_credentials_ready(raw) or key),
        "name": raw.get("name", "Remedy"),
        "user_name": str(raw.get("user_name") or "").strip(),
        "persona": raw.get("persona", "default"),
        "project_path": raw.get("project_path") or "",
        "access_scope": raw.get("access_scope", "project"),
        "launch_at_login": bool(raw.get("launch_at_login", False)),
        "start_in_tray": bool(raw.get("start_in_tray", False)),
        "close_to_tray": bool(raw.get("close_to_tray", False)),
        "harness_mode": raw.get("harness_mode", "auto"),
        "harness_min_context_pct": float(raw.get("harness_min_context_pct", 0.75)),
        "harness_max_context_pct": float(raw.get("harness_max_context_pct", 0.92)),
        "thinking_level": str(raw.get("thinking_level") or "high").lower(),
        "approval_mode": str(raw.get("approval_mode") or "ask").lower(),
        "tool_process": normalize_tool_process(raw),
        "web_tools_enabled": bool(raw.get("web_tools_enabled", False)),
        "http_bootstrap": bool(raw.get("http_bootstrap", True)),
        "allow_skill_creation": bool(raw.get("allow_skill_creation", True)),
        "auto_approve_threshold": float(raw.get("auto_approve_threshold", 0.8)),
        "log_level": str(raw.get("log_level") or "INFO").upper(),
        "sarcasm_mode": bool(raw.get("sarcasm_mode", False)),
        "skills_active_budget": int(raw.get("skills_active_budget") or 80),
        "browser_home_url": normalize_browser_home_url(raw.get("browser_home_url")),
        "enabled_providers": raw.get("enabled_providers"),
        "enabled_models": raw.get("enabled_models") or {},
        "last_model_by_provider": raw.get("last_model_by_provider") or {},
        "enabled_channels": raw.get("enabled_channels") or ["cli"],
        "vision_enabled": bool(vision.get("enabled", True)),
        "vision_model_id": str(vision.get("model_id") or DEFAULT_MODEL_ID),
        "vision_force_decode": bool(vision.get("force_decode")),
        "setup_completed": bool(raw.get("setup_completed", False)),
        "config_path": str(_find_config_path() or _default_config_path()),
    }


async def apply_settings_update(
    updates: dict[str, Any],
    *,
    runtime: Any = None,
    gateway: Any = None,
    memory: Any = None,
    model_discovery_cache: dict | None = None,
) -> dict[str, Any]:
    """Apply a partial settings patch. Returns a status dict (never raw secrets).

    Raises ``ValueError`` for empty/invalid patches; ``OSError`` on write failure.
    """
    from remedy.interfaces.config import (
        migrate_provider_keys,
        resolve_provider_api_key,
        set_provider_key,
    )
    from remedy.interfaces.secret_store import scrub_config_secrets

    if not isinstance(updates, dict) or not updates:
        raise ValueError("updates must be a non-empty object")

    # Drop unknown keys (and nested Nones later via model if used)
    clean_in: dict[str, Any] = {}
    for k, v in updates.items():
        if k in SETTABLE_KEYS and v is not None:
            clean_in[k] = v
    if not clean_in:
        raise ValueError(
            "no recognized settings keys in patch. "
            f"Known keys include: {', '.join(sorted(SETTABLE_KEYS)[:20])}…"
        )

    config_path = _find_config_path()
    if config_path is None:
        config_path = _default_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

    raw_cfg = load_config()
    cfg = migrate_provider_keys(raw_cfg if isinstance(raw_cfg, dict) else {})
    prev_provider = str(cfg.get("llm_provider") or "").strip().lower()
    patch = dict(clean_in)

    if "llm_api_key" in patch and not patch["llm_api_key"]:
        del patch["llm_api_key"]

    merged = {**cfg, **patch}
    # Snap garbage / cross-provider model ids before persist (never store junk).
    provider, model, base_url = normalize_llm_settings(
        merged.get("llm_provider"),
        merged.get("llm_model"),
        merged.get("llm_base_url"),
    )
    # Closed catalogs: if client sent an explicit invalid model, still snap
    # even when normalize kept a native-looking prefix incorrectly.
    if "llm_model" in clean_in or "llm_provider" in clean_in:
        try:
            from remedy.interfaces.config import validate_provider_model

            model = validate_provider_model(provider, model)
        except ValueError:
            # Fall back to normalize default for this provider
            provider, model, base_url = normalize_llm_settings(
                provider, None, base_url
            )
    patch["llm_provider"] = provider
    patch["llm_model"] = model
    patch["llm_base_url"] = base_url
    if prev_provider and prev_provider != provider:
        patch["last_llm_provider"] = prev_provider

    if "project_path" in patch and patch["project_path"] is not None:
        from remedy.core.workspace import ensure_project_dir, resolve_project_path

        raw_pp = str(patch["project_path"]).strip()
        if raw_pp and raw_pp not in (".", "./"):
            try:
                patch["project_path"] = str(
                    ensure_project_dir(resolve_project_path(raw_pp))
                )
            except Exception:
                patch["project_path"] = str(resolve_project_path(raw_pp))
        else:
            patch["project_path"] = ""

    if "access_scope" in patch and patch["access_scope"] is not None:
        from remedy.core.workspace import normalize_access_scope

        patch["access_scope"] = normalize_access_scope(str(patch["access_scope"]))

    if "harness_mode" in patch and patch["harness_mode"] is not None:
        hm = str(patch["harness_mode"]).strip().lower()
        patch["harness_mode"] = hm if hm in ("off", "manual", "auto") else "auto"

    if "enabled_providers" in patch and patch["enabled_providers"] is not None:
        raw_ep = patch["enabled_providers"]
        if isinstance(raw_ep, list):
            patch["enabled_providers"] = [
                str(x).strip().lower() for x in raw_ep if str(x).strip()
            ]
        elif isinstance(raw_ep, str) and raw_ep.strip():
            patch["enabled_providers"] = [
                x.strip().lower() for x in raw_ep.split(",") if x.strip()
            ]
        else:
            patch["enabled_providers"] = []
        # Never drop zero-setup demo from the taskbar/settings picker list.
        if (
            isinstance(patch["enabled_providers"], list)
            and "demo" not in patch["enabled_providers"]
        ):
            patch["enabled_providers"] = ["demo", *patch["enabled_providers"]]

    if "enabled_models" in patch and patch["enabled_models"] is not None:
        raw_em = patch["enabled_models"]
        if isinstance(raw_em, dict):
            clean_em: dict[str, list[str]] = {}
            for k, v in raw_em.items():
                if isinstance(v, list):
                    clean_em[str(k).lower()] = [str(x) for x in v if str(x).strip()]
            patch["enabled_models"] = clean_em
        else:
            patch.pop("enabled_models", None)

    if "last_model_by_provider" in patch and patch["last_model_by_provider"] is not None:
        raw_lm = patch["last_model_by_provider"]
        if isinstance(raw_lm, dict):
            patch["last_model_by_provider"] = {
                str(k).lower(): str(v)
                for k, v in raw_lm.items()
                if str(k).strip() and str(v).strip()
            }
        else:
            patch.pop("last_model_by_provider", None)

    if "skills_active_budget" in patch and patch["skills_active_budget"] is not None:
        try:
            b = int(patch["skills_active_budget"])
            patch["skills_active_budget"] = max(10, min(500, b))
        except (TypeError, ValueError):
            patch["skills_active_budget"] = 80

    if "thinking_level" in patch and patch["thinking_level"] is not None:
        tl = str(patch["thinking_level"]).strip().lower()
        patch["thinking_level"] = tl if tl in ("off", "low", "medium", "high") else "high"

    if "approval_mode" in patch and patch["approval_mode"] is not None:
        am = str(patch["approval_mode"]).strip().lower()
        # Friendly aliases
        if am in ("yolo", "full", "owner", "trust"):
            am = "auto"
        if am in ("confirm", "manual", "safe"):
            am = "ask"
        patch["approval_mode"] = am if am in ("ask", "auto") else "ask"
        try:
            from remedy.core.approvals import APPROVALS

            APPROVALS.set_mode(patch["approval_mode"])
        except Exception:
            pass

    if "web_tools_enabled" in patch and patch["web_tools_enabled"] is not None:
        patch["web_tools_enabled"] = _as_bool(patch["web_tools_enabled"])

    if "http_bootstrap" in patch and patch["http_bootstrap"] is not None:
        patch["http_bootstrap"] = _as_bool(patch["http_bootstrap"])

    if "browser_home_url" in patch and patch["browser_home_url"] is not None:
        patch["browser_home_url"] = normalize_browser_home_url(patch["browser_home_url"])

    assistant_update = patch.pop("assistant", None)

    if "allow_skill_creation" in patch and patch["allow_skill_creation"] is not None:
        patch["allow_skill_creation"] = _as_bool(patch["allow_skill_creation"])

    if "sarcasm_mode" in patch and patch["sarcasm_mode"] is not None:
        patch["sarcasm_mode"] = _as_bool(patch["sarcasm_mode"])

    if "auto_approve_threshold" in patch and patch["auto_approve_threshold"] is not None:
        try:
            t = float(patch["auto_approve_threshold"])
            patch["auto_approve_threshold"] = max(0.0, min(1.0, t))
        except (TypeError, ValueError):
            patch["auto_approve_threshold"] = 0.8

    if "log_level" in patch and patch["log_level"] is not None:
        ll = str(patch["log_level"]).strip().upper()
        patch["log_level"] = ll if ll in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO"

    if "harness_min_context_pct" in patch and patch["harness_min_context_pct"] is not None:
        try:
            v = float(patch["harness_min_context_pct"])
            patch["harness_min_context_pct"] = max(0.05, min(0.95, v))
        except (TypeError, ValueError):
            patch.pop("harness_min_context_pct", None)

    if "harness_max_context_pct" in patch and patch["harness_max_context_pct"] is not None:
        try:
            v = float(patch["harness_max_context_pct"])
            patch["harness_max_context_pct"] = max(0.1, min(0.99, v))
        except (TypeError, ValueError):
            patch.pop("harness_max_context_pct", None)

    if "tool_process" in patch and patch["tool_process"] is not None:
        patch["tool_process"] = normalize_tool_process(raw=patch["tool_process"])
    elif "show_tool_calls" in patch and patch["show_tool_calls"] is not None:
        patch["tool_process"] = "full" if _as_bool(patch["show_tool_calls"]) else "off"
    patch.pop("show_tool_calls", None)

    vision_enabled = patch.pop("vision_enabled", None)
    vision_model_id = patch.pop("vision_model_id", None)
    vision_force_decode = patch.pop("vision_force_decode", None)
    if (
        vision_enabled is not None
        or vision_model_id is not None
        or vision_force_decode is not None
    ):
        vision_tbl = (
            dict(cfg.get("vision") or {}) if isinstance(cfg.get("vision"), dict) else {}
        )
        if vision_enabled is not None:
            vision_tbl["enabled"] = _as_bool(vision_enabled)
        if vision_model_id is not None and str(vision_model_id).strip():
            vision_tbl["model_id"] = str(vision_model_id).strip()
        if vision_force_decode is not None:
            vision_tbl["force_decode"] = _as_bool(vision_force_decode)
        cfg["vision"] = vision_tbl

    messengers_update = patch.pop("messengers", None)
    if "enabled_channels" in patch and patch["enabled_channels"] is not None:
        from remedy.interfaces.messenger_settings import normalize_enabled_channels

        patch["enabled_channels"] = normalize_enabled_channels(patch["enabled_channels"])

    if "setup_completed" in patch and patch["setup_completed"] is not None:
        patch["setup_completed"] = _as_bool(patch["setup_completed"])

    if "launch_at_login" in patch and patch["launch_at_login"] is not None:
        patch["launch_at_login"] = _as_bool(patch["launch_at_login"])
    if "start_in_tray" in patch and patch["start_in_tray"] is not None:
        patch["start_in_tray"] = _as_bool(patch["start_in_tray"])
    if "close_to_tray" in patch and patch["close_to_tray"] is not None:
        patch["close_to_tray"] = _as_bool(patch["close_to_tray"])

    incoming_key = patch.pop("llm_api_key", None)
    cfg.update(patch)
    home = cfg.get("home_dir")
    home_path = Path(home).expanduser() if home else None

    if incoming_key is not None and str(incoming_key).strip():
        set_provider_key(cfg, provider, str(incoming_key).strip(), home=home_path)
        if provider == "xai":
            try:
                from remedy.interfaces.xai_auth import save_api_key

                save_api_key(str(incoming_key).strip(), home=home_path)
            except Exception as exc:
                logger.debug("xAI settings key sync: %s", exc)

    if isinstance(assistant_update, dict):
        try:
            from remedy.assistant.store import get_assistant_store

            astore = get_assistant_store(home_path)
            ap = dict(assistant_update)
            apatch: dict = {}
            for k in (
                "enabled",
                "timezone",
                "money_disclaimer_accepted",
                "privacy_ai_accepted",
                "account_access_accepted",
                "default_calendar_account",
                "default_mail_account",
            ):
                if k in ap:
                    apatch[k] = ap[k]
            if isinstance(ap.get("brief"), dict):
                apatch["brief"] = ap["brief"]
            if apatch:
                astore.patch_prefs(**apatch)
            prefs = astore.get_prefs()
            cfg["assistant"] = {
                "enabled": prefs.enabled,
                "timezone": prefs.timezone,
                "money_disclaimer_accepted": prefs.money_disclaimer_accepted,
                "brief": prefs.brief.to_dict(),
            }
        except Exception as exc:
            logger.warning("assistant prefs save failed: %s", exc)

    if isinstance(messengers_update, dict):
        try:
            from remedy.interfaces.messenger_settings import apply_messengers_update

            apply_messengers_update(cfg, messengers_update, home_path=home_path)
        except Exception:
            logger.exception("messenger settings update failed")
        try:
            from remedy.gateway.channel_hot_reload import reload_messenger_channels

            await reload_messenger_channels(gateway, cfg)
        except Exception:
            logger.debug("messenger hot-reload skipped", exc_info=True)

    if "user_name" in patch and patch["user_name"] is not None:
        uname = str(patch["user_name"]).strip()
        patch["user_name"] = uname
        cfg["user_name"] = uname
        if memory is not None and uname:
            try:
                profile = await memory.get_or_create_profile()
                profile.display_name = uname
                await memory.save_user_profile(profile)
            except Exception as exc:
                logger.debug("sync user_name → profile: %s", exc)

    cfg = scrub_config_secrets(cfg)
    cfg["llm_api_key"] = ""
    cfg.pop("provider_keys", None)
    _write_config(config_path, cfg)

    if isinstance(model_discovery_cache, dict):
        model_discovery_cache.clear()

    api_key_for_runtime = resolve_provider_api_key(cfg, provider)
    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key_for_runtime or None,
        persona=patch.get("persona"),
        name=patch.get("name"),
        project_path=patch.get("project_path", cfg.get("project_path")),
        access_scope=cfg.get("access_scope"),
        harness_mode=cfg.get("harness_mode"),
        harness_min_context_pct=cfg.get("harness_min_context_pct"),
        harness_max_context_pct=cfg.get("harness_max_context_pct"),
        thinking_level=cfg.get("thinking_level"),
        approval_mode=cfg.get("approval_mode"),
    )
    # Keep runtime.config flags in sync for tools that read attributes
    if runtime is not None and hasattr(runtime, "config") and runtime.config is not None:
        for attr in (
            "web_tools_enabled",
            "http_bootstrap",
            "tool_process",
            "sarcasm_mode",
            "allow_skill_creation",
        ):
            if attr in cfg:
                try:
                    setattr(runtime.config, attr, cfg[attr])
                except Exception:
                    pass

    changes = list(patch.keys())
    if vision_enabled is not None or vision_model_id is not None or vision_force_decode is not None:
        for vk, vv in (
            ("vision_enabled", vision_enabled),
            ("vision_model_id", vision_model_id),
            ("vision_force_decode", vision_force_decode),
        ):
            if vv is not None and vk not in changes:
                changes.append(vk)
    if assistant_update is not None and "assistant" not in changes:
        changes.append("assistant")
    if messengers_update is not None and "messengers" not in changes:
        changes.append("messengers")

    snap = public_settings_snapshot(cfg)
    out = {
        "status": "saved",
        "changes": changes,
        "config_path": str(config_path),
        "message": f"Applied settings: {', '.join(changes)}",
    }
    for k in (
        "llm_provider",
        "llm_model",
        "llm_base_url",
        "persona",
        "project_path",
        "access_scope",
        "thinking_level",
        "approval_mode",
        "user_name",
        "tool_process",
        "web_tools_enabled",
        "http_bootstrap",
        "vision_enabled",
        "vision_model_id",
        "vision_force_decode",
        "name",
        "launch_at_login",
        "start_in_tray",
        "close_to_tray",
        "harness_mode",
    ):
        if k in snap:
            out[k] = snap[k]
    return out


def _as_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return bool(v)


# Human phrases → settings patch (used by agent tool for "set X up")
SETUP_ALIASES: dict[str, dict[str, Any]] = {
    "web tools": {"web_tools_enabled": True},
    "web_tools": {"web_tools_enabled": True},
    "web fetch": {"web_tools_enabled": True},
    "enable web": {"web_tools_enabled": True},
    "disable web": {"web_tools_enabled": False},
    "auto approval": {"approval_mode": "auto"},
    "approval auto": {"approval_mode": "auto"},
    "full power": {"approval_mode": "auto"},
    "work until done": {"approval_mode": "auto"},
    "ask approval": {"approval_mode": "ask"},
    "safe mode": {"approval_mode": "ask"},
    "vision": {"vision_enabled": True, "vision_model_id": "smolvlm2-2.2b"},
    "smolvlm": {"vision_enabled": True, "vision_model_id": "smolvlm2-2.2b"},
    "smolvlm2": {"vision_enabled": True, "vision_model_id": "smolvlm2-2.2b"},
    "force decode": {"vision_force_decode": True},
    "http bootstrap": {"http_bootstrap": True},
    "browser bootstrap": {"http_bootstrap": True},
    "thinking low": {"thinking_level": "low"},
    "thinking medium": {"thinking_level": "medium"},
    "thinking high": {"thinking_level": "high"},
    "thinking off": {"thinking_level": "off"},
    "tool process full": {"tool_process": "full"},
    "tool process off": {"tool_process": "off"},
    "sarcasm": {"sarcasm_mode": True},
    "no sarcasm": {"sarcasm_mode": False},
    "full access": {"access_scope": "full"},
    "home access": {"access_scope": "home"},
    "project access": {"access_scope": "project"},
    "setup done": {"setup_completed": True},
    "finish setup": {"setup_completed": True},
}


def resolve_setup_phrase(phrase: str) -> dict[str, Any] | None:
    """Map a short human setup phrase to a settings patch, if known."""
    p = " ".join(str(phrase or "").strip().lower().split())
    if not p:
        return None
    if p in SETUP_ALIASES:
        return dict(SETUP_ALIASES[p])
    # soft contains match for longer user wording
    for key, patch in SETUP_ALIASES.items():
        if key in p or p in key:
            return dict(patch)
    return None
