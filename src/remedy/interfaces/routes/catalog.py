"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import logging
import os
import time
from contextlib import suppress
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException

from remedy.core.security import safe_path
from remedy.home import default_home
from remedy.interfaces.api_models import (
    CommandRequest,
)
from remedy.interfaces.api_support import (
    _BUILTIN_AGENTS,
    _BUILTIN_COMMANDS,
    handle_slash_command,
    load_config,
)
from remedy.interfaces.config import (
    _is_local_url,
    catalog_models_for_provider,
    normalize_llm_settings,
)
from remedy.interfaces.model_discovery import (
    DiscoveryResult,
    choose_default,
    discover_models,
    ollama_base_url_from_env,
)

logger = logging.getLogger(__name__)

# Gateway noise that must never appear under Demo even if discovery leaks.
_DEMO_BLOCK_SUBSTR = (
    "image",
    "flux",
    "dall",
    "sdxl",
    "kling",
    "seedance",
    "veo",
    "video",
    "tts",
    "whisper",
    "embed",
    "moderation",
    "omni",  # multimodal/non-chat demo ids on llm7
)


def _demo_model_allowed(mid: str, catalog: list[dict]) -> bool:
    """Only curated demo catalog ids; block image/video and foreign brands."""
    m = (mid or "").strip()
    if not m:
        return False
    allow = {str(c.get("id") or "") for c in catalog if c.get("id")}
    if allow and m not in allow:
        return False
    low = m.lower()
    return not any(s in low for s in _DEMO_BLOCK_SUBSTR)


def register_catalog_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- commands (slash palette) --------------------------------------------
    @app.get("/api/commands")
    async def list_commands():
        return {"commands": _BUILTIN_COMMANDS}

    @app.post("/api/sessions/{session_id}/command")
    async def execute_command(session_id: str, req: CommandRequest):
        result = await handle_slash_command(
            req.command, session_id, memory, runtime=runtime
        )
        return {"session_id": session_id, "command": req.command, **result}

    # -- models & agents -----------------------------------------------------
    @app.get("/api/models")
    async def list_models(
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        """List models for a provider (default: currently configured).

        Query ``?provider=deepseek`` (etc.) discovers against that provider's
        base_url + stored key without changing the active runtime selection.
        ``?base_url=`` / ``?api_key=`` are unsaved overrides so the Settings
        form can list models before Save. Live discovery is the source of
        truth; the curated catalog is fallback only, and ``discovery`` in the
        response says what was tried and why it fell back.
        """
        from remedy.interfaces.config import (
            _CLOSED_PROVIDERS,
            PROVIDER_CATALOG,
            _native_model_id_for_provider,
            infer_provider_from_model,
            resolve_provider_api_key,
        )

        override_url = (base_url or "").strip()
        override_key = (api_key or "").strip()

        cfg = load_config()
        active_provider = (
            (runtime._llm_provider if runtime is not None else None)
            or cfg.get("llm_provider")
            or os.environ.get("REMEDY_LLM_PROVIDER")
            or "openai"
        )
        active_id = (
            (runtime._llm_model if runtime is not None else None)
            or cfg.get("llm_model")
            or os.environ.get("REMEDY_LLM_MODEL")
            or ""
        )
        active_url = (
            (runtime._llm_base_url if runtime is not None else None)
            or cfg.get("llm_base_url")
            or os.environ.get("REMEDY_LLM_BASE_URL")
            or ""
        )

        # Optional: discover for a different connected provider (status-bar switch).
        req_provider = (provider or "").strip().lower()
        if req_provider and req_provider in PROVIDER_CATALOG:
            configured_provider = req_provider
            meta = PROVIDER_CATALOG.get(req_provider) or {}
            base_url = str(meta.get("base_url") or "")
            if req_provider == "ollama":
                base_url = ollama_base_url_from_env() or base_url
            # The saved URL for the active provider beats the catalog default
            # (custom Ollama host, proxy) — otherwise the picker probes one
            # host while chat talks to another.
            if req_provider == str(active_provider or "").lower() and active_url:
                base_url = str(active_url)
            last_by = cfg.get("last_model_by_provider") or {}
            if isinstance(last_by, dict) and last_by.get(req_provider):
                configured_id = str(last_by.get(req_provider) or "")
            else:
                models_meta = list(meta.get("models") or [])
                configured_id = str(models_meta[0]["id"]) if models_meta else ""
            # Prefer stored key for that provider (not whatever runtime is using).
            api_key = ""
            with suppress(Exception):
                api_key = str(resolve_provider_api_key(cfg, req_provider) or "")
        else:
            configured_provider = str(active_provider or "openai").lower()
            configured_id = str(active_id or "")
            base_url = str(active_url or "")
            api_key = ""
            if runtime is not None:
                api_key = getattr(runtime, "_llm_api_key", "") or ""
            if not api_key:
                api_key = str(
                    cfg.get("llm_api_key") or os.environ.get("REMEDY_LLM_API_KEY") or ""
                )
            with suppress(Exception):
                if not api_key:
                    api_key = str(
                        resolve_provider_api_key(cfg, configured_provider) or ""
                    )

        # Whether the id came from config/runtime (a real selection) rather
        # than from normalization filling a blank with a catalog placeholder.
        explicit_id = bool(configured_id)
        # Soft-normalize for closed providers only when shaping default id display
        # — never persist from GET.
        _np, _nm, _nu = normalize_llm_settings(configured_provider, configured_id, base_url)
        if configured_provider not in ("openrouter", "custom", "ollama", "poe"):
            configured_provider, configured_id, base_url = _np, _nm, _nu
        elif not base_url:
            base_url = _nu
        if not configured_id:
            configured_id = _nm

        catalog = catalog_models_for_provider(configured_provider)
        catalog_meta = PROVIDER_CATALOG.get(configured_provider) or {}

        # Unsaved overrides from the Settings form (list before Save).
        if override_url:
            base_url = override_url
        if override_key:
            api_key = override_key

        # Discovery cache (process-local).
        # Success TTL is long (lists rarely change). Empty/failed probes use a short
        # TTL so a boot-time timeout does not lock every provider to curated stubs.
        key_sig = f"{len(api_key)}:{api_key[-4:]}" if api_key else "-"
        cache_key = f"{configured_provider}|{base_url}|{key_sig}"
        cache = getattr(app.state, "_model_discovery_cache", None)
        if cache is None:
            app.state._model_discovery_cache = {}
            cache = app.state._model_discovery_cache
        inflight = getattr(app.state, "_model_discovery_inflight", None)
        if inflight is None:
            app.state._model_discovery_inflight = {}
            inflight = app.state._model_discovery_inflight
        now = time.time()

        # Live discovery is the source of truth; curated catalog is fallback.
        # Opt out: REMEDY_LIVE_MODELS=0. Force on: REMEDY_LIVE_MODELS=1.
        live_env = str(os.environ.get("REMEDY_LIVE_MODELS", "")).strip().lower()
        live_off = live_env in ("0", "false", "no", "off")
        live_force = live_env in ("1", "true", "yes", "on")
        keyless = "none" in (catalog_meta.get("auth") or []) or configured_provider in (
            "ollama", "rmb", "llamacpp", "demo"
        )
        real_key = bool(api_key) and api_key not in ("local", "unused", "none")
        # Skip remote discovery without a key (would 401 and burn the timeout).
        can_live = bool(base_url) and (
            live_force or keyless or _is_local_url(base_url) or real_key
        )

        disc = DiscoveryResult(
            attempted=False, url=base_url, error="no key" if base_url else "no base URL"
        )
        if not live_off and can_live:
            cached = cache.get(cache_key)
            if cached and isinstance(cached[1], DiscoveryResult):
                ttl = 180.0 if cached[1].ok else 12.0
                if (now - cached[0]) < ttl:
                    disc = DiscoveryResult(**{**cached[1].__dict__})
                    disc.models = [dict(r) for r in cached[1].models]
                    disc.loaded = list(cached[1].loaded)
                    disc.cached = True
            if not disc.cached:
                import asyncio

                existing = inflight.get(cache_key)
                if existing is not None:
                    try:
                        disc = await existing
                    except Exception:
                        disc = DiscoveryResult(
                            attempted=True, url=base_url, error="discovery failed"
                        )
                else:
                    loop = asyncio.get_event_loop()
                    fut: asyncio.Future = loop.create_future()
                    inflight[cache_key] = fut
                    try:
                        disc = await discover_models(
                            base_url, api_key, provider_hint=configured_provider
                        )
                    except Exception as exc:  # pragma: no cover - never raises
                        disc = DiscoveryResult(
                            attempted=True, url=base_url, error=f"{type(exc).__name__}: {exc}"
                        )
                    finally:
                        if not fut.done():
                            fut.set_result(disc)
                        inflight.pop(cache_key, None)
                    cache[cache_key] = (now, disc)
                if not disc.ok:
                    logger.info(
                        "Model discovery failed for %s (%s): %s %s",
                        configured_provider,
                        disc.url,
                        disc.status or "",
                        disc.error or "",
                    )

        discovered: list[dict] = []
        if disc.ok:
            for r in disc.models:
                row = dict(r)
                row["provider"] = configured_provider
                row["default"] = False
                discovered.append(row)
                ctx = row.get("context_window")
                if isinstance(ctx, int) and ctx > 0:
                    with suppress(Exception):
                        from remedy.nanoswarm.token_nanobot import cache_context_window

                        cache_context_window(base_url, str(row["id"]), ctx)

        # Merge. Live endpoint rows win (catalog names/flags enrich them). The
        # curated catalog is used only when discovery did not succeed — a row the
        # provider's own endpoint no longer lists is most likely retired.
        catalog_by_id = {str(c.get("id")): c for c in catalog if c.get("id")}
        catalog_flags = {
            str(m.get("id")): m
            for m in (catalog_meta.get("models") or [])
            if isinstance(m, dict) and m.get("id")
        }
        seen: set[str] = set()
        merged: list[dict] = []
        if configured_provider == "demo":
            # Demo: curated allowlist, validated against the gateway when reachable.
            live_ids = {str(r["id"]) for r in discovered}
            for c in catalog:
                mid = str(c.get("id") or "")
                if not mid or not _demo_model_allowed(mid, catalog):
                    continue
                if disc.ok and live_ids and mid not in live_ids:
                    logger.info("Demo model %s no longer served by the gateway", mid)
                    continue
                seen.add(mid)
                merged.append(
                    {**c, "default": False, "source": "endpoint" if disc.ok else "catalog"}
                )
            if not merged:
                merged = [{**c, "default": False, "source": "catalog"} for c in catalog]
        else:
            rows = discovered if disc.ok else []
            for m in rows:
                mid = str(m.get("id") or "")
                if not mid or mid in seen:
                    continue
                # Endpoint said it is not a chat model (embeddings, tts, image…)
                # or the id plainly looks like one when the host says nothing.
                if not m.get("chat", True):
                    continue
                # A closed vendor's list will not carry another closed vendor's
                # ids (a Claude id on DeepSeek means a misconfigured proxy and
                # would 404 on the first chat). Open-weight families (llama,
                # qwen, gemma…) are served by Groq/Google for real — keep them.
                if configured_provider in _CLOSED_PROVIDERS:
                    owner = infer_provider_from_model(mid)
                    if (
                        owner
                        and owner != configured_provider
                        and owner in _CLOSED_PROVIDERS
                        and not _native_model_id_for_provider(configured_provider, mid)
                    ):
                        continue
                seen.add(mid)
                cat = catalog_by_id.get(mid) or {}
                flags = catalog_flags.get(mid) or {}
                row = {
                    **m,
                    "id": mid,
                    "name": str(cat.get("name") or m.get("name") or mid),
                    "provider": configured_provider,
                    "default": False,
                    "source": "endpoint",
                }
                if "vision" not in row and "vision" in flags:
                    row["vision"] = bool(flags.get("vision"))
                merged.append(row)
            if not merged:
                for c in catalog:
                    mid = str(c.get("id") or "")
                    if not mid or mid in seen:
                        continue
                    seen.add(mid)
                    flags = catalog_flags.get(mid) or {}
                    row = {**c, "default": False, "source": "catalog"}
                    if "vision" in flags:
                        row["vision"] = bool(flags.get("vision"))
                    merged.append(row)

        if not merged:
            merged = [
                {
                    "id": configured_id or "default",
                    "name": configured_id or "default",
                    "provider": configured_provider,
                    "default": True,
                    "source": "config",
                }
            ]

        ids = {str(m["id"]) for m in merged}
        flexible = configured_provider in (
            "openrouter", "custom", "ollama", "poe", "rmb", "llamacpp"
        )
        if configured_id and configured_id not in ids:
            # Keep the saved selection visible when we could not verify it, or
            # when the provider accepts arbitrary ids (local GGUF stems, bots).
            keep = explicit_id and ((not disc.ok) or flexible)
            if configured_provider == "demo":
                keep = (
                    explicit_id
                    and _demo_model_allowed(configured_id, catalog)
                    and not disc.ok
                )
            if keep:
                merged.insert(
                    0,
                    {
                        "id": configured_id,
                        "name": configured_id,
                        "provider": configured_provider,
                        "default": True,
                        "source": "config",
                    },
                )
                ids.add(configured_id)

        last_by = cfg.get("last_model_by_provider") or {}
        preferred: list[str | None] = [
            configured_id,
            str(last_by.get(configured_provider) or "") if isinstance(last_by, dict) else "",
            str(catalog[0]["id"]) if catalog else "",
        ]
        default_id = (
            choose_default(merged, preferred=preferred, loaded=disc.loaded) or "default"
        )
        for m in merged:
            m["default"] = m["id"] == default_id
            m.pop("chat", None)

        return {
            "models": merged,
            "default": default_id,
            "provider": configured_provider,
            "base_url": base_url,
            "discovery": disc.as_dict(),
            "loaded": list(disc.loaded),
        }

    @app.get("/api/agents")
    async def list_agents():
        return {"agents": _BUILTIN_AGENTS}

    # -- custom commands (markdown-based, ~/.remedy/commands/) ----------------
    @app.get("/api/commands/custom")
    async def list_custom_commands():
        cmd_dir = default_home() / "commands"
        if not cmd_dir.exists():
            return {"commands": []}
        commands: list[dict] = []
        for f in sorted(cmd_dir.glob("*.md")):
            name = f.stem
            desc = ""
            # try to read YAML frontmatter
            content = f.read_text(encoding="utf-8", errors="replace")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    try:
                        fm = yaml.safe_load(parts[1])
                        if isinstance(fm, dict):
                            # Was fm.get("description", name): a command with
                            # frontmatter showed its description where its name
                            # belonged, and the file stem was lost.
                            name = fm.get("name", name)
                            desc = fm.get("description", "")
                    except Exception:
                        pass
            commands.append({"name": name, "description": desc, "file": str(f)})
        return {"commands": commands}

    @app.get("/api/commands/custom/{name}")
    async def get_custom_command(name: str):
        cmd_dir = default_home() / "commands"
        # safe_path(user_input, base_dir) — never invert (was always traversal).
        stem = Path(str(name or "")).name.strip()
        if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
            raise HTTPException(400, "Invalid command name")
        try:
            path = safe_path(f"{stem}.md", base_dir=cmd_dir)
        except Exception as exc:
            raise HTTPException(400, f"Invalid command name: {exc}") from exc
        if not path.exists():
            raise HTTPException(404, f"Command '{name}' not found")
        return {"content": path.read_text(encoding="utf-8", errors="replace")}

    # -- custom agents (markdown-based, ~/.remedy/agents/) -------------------
    @app.get("/api/agents/custom")
    async def list_custom_agents():
        agent_dir = default_home() / "agents"
        if not agent_dir.exists():
            return {"agents": []}
        agents: list[dict] = []
        for f in sorted(agent_dir.glob("*.md")):
            name = f.stem
            desc = ""
            content = f.read_text(encoding="utf-8", errors="replace")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    try:
                        fm = yaml.safe_load(parts[1])
                        if isinstance(fm, dict):
                            name = fm.get("name", name)
                            desc = fm.get("description", "")
                    except Exception:
                        pass
            agents.append({"name": name, "description": desc, "file": str(f)})
        return {"agents": agents}

    @app.get("/api/agents/custom/{name}")
    async def get_custom_agent(name: str):
        agent_dir = default_home() / "agents"
        stem = Path(str(name or "")).name.strip()
        if not stem or stem in (".", "..") or "/" in stem or "\\" in stem:
            raise HTTPException(400, "Invalid agent name")
        try:
            path = safe_path(f"{stem}.md", base_dir=agent_dir)
        except Exception as exc:
            raise HTTPException(400, f"Invalid agent name: {exc}") from exc
        if not path.exists():
            raise HTTPException(404, f"Agent '{name}' not found")
        return {"content": path.read_text(encoding="utf-8", errors="replace")}

