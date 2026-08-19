"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import logging
import os
import time
from contextlib import suppress
from pathlib import Path

import aiohttp
import yaml
from fastapi import FastAPI, HTTPException

from remedy.core.security import safe_path
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


def _looks_like_media_model(mid: str) -> bool:
    """True for image/video/audio-only bot ids (keep chat pickers clean)."""
    low = (mid or "").lower()
    return any(
        s in low
        for s in (
            "imagine-image",
            "imagine-video",
            "gpt-image",
            "flux",
            "dall-e",
            "dalle",
            "stable-diffusion",
            "sdxl",
            "kling",
            "seedance",
            "veo",
            "sora",
            "runway",
            "tts",
            "whisper",
            "embedding",
            "moderation",
        )
    )


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
    async def list_models(provider: str | None = None):
        """List models for a provider (default: currently configured).

        Query ``?provider=deepseek`` (etc.) discovers against that provider's
        base_url + stored key without changing the active runtime selection.
        Live GET {base}/models is preferred; curated catalog is fallback only.
        """
        from remedy.interfaces.config import (
            PROVIDER_CATALOG,
            resolve_provider_api_key,
        )

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

        # Discovery cache (process-local).
        # Success TTL is long (lists rarely change). Empty/failed probes use a short
        # TTL so a boot-time timeout does not lock every provider to curated stubs.
        cache_key = f"{configured_provider}|{base_url}|{bool(api_key)}"
        cache = getattr(app.state, "_model_discovery_cache", None)
        if cache is None:
            app.state._model_discovery_cache = {}
            cache = app.state._model_discovery_cache
        inflight = getattr(app.state, "_model_discovery_inflight", None)
        if inflight is None:
            app.state._model_discovery_inflight = {}
            inflight = app.state._model_discovery_inflight
        now = time.time()
        cached = cache.get(cache_key)
        discovered: list[dict] = []
        from_cache = False
        if cached:
            cached_at, cached_list, cached_ok = cached[0], cached[1], (
                cached[2] if len(cached) > 2 else bool(cached[1])
            )
            ttl = 180.0 if cached_ok else 12.0
            if (now - cached_at) < ttl:
                from_cache = True
                discovered = list(cached_list)

        verify_ssl = not _is_local_url(base_url)

        # Live GET {base_url}/models is the source of truth for OpenAI-compatible
        # providers (DeepSeek, xAI, OpenAI, Groq, Poe, …). Curated catalogs are
        # fallback only when discovery fails or returns empty.
        #
        # Opt out: REMEDY_LIVE_MODELS=0. Force on: REMEDY_LIVE_MODELS=1.
        live_env = str(os.environ.get("REMEDY_LIVE_MODELS", "")).strip().lower()
        live_off = live_env in ("0", "false", "no", "off")
        live_force = live_env in ("1", "true", "yes", "on")
        # Anthropic chat is Messages API; model *list* is GET /v1/models
        # with x-api-key (handled separately). Other providers use OpenAI /models.
        openai_compat = configured_provider != "anthropic"
        # Demo uses a public gateway whose /models lists image/video, promo, and
        # paid-key models that fail with the guest dummy key — never auto-merge.
        demo_live_blocked = configured_provider == "demo" and not live_force
        want_live = (
            (not live_off)
            and (not demo_live_blocked)
            and (
                live_force
                or openai_compat
                or configured_provider == "anthropic"
                or _is_local_url(base_url)
                or configured_provider in ("ollama", "openrouter", "custom", "poe")
            )
        )
        # Skip remote discovery without a key (would 401 and burn the timeout).
        # Demo: dummy "unused" key would otherwise enable full gateway dump.
        can_live = bool(base_url) and (not demo_live_blocked) and (
            configured_provider == "ollama"
            or _is_local_url(base_url)
            or (api_key and api_key != "local" and api_key != "unused")
            or live_force
        )

        async def _discover_openai_compat() -> list[dict]:
            """GET {base}/models once; empty list means soft-fail (use catalog)."""
            found: list[dict] = []
            models_url = base_url.rstrip("/") + "/models"
            headers: dict[str, str] = {}
            if api_key and api_key != "local":
                headers["Authorization"] = f"Bearer {api_key}"
            # 4.5s: boot piles several discoveries; 1.8s timed out under load and
            # then cached empty results → "stuck on curated DeepSeek stubs".
            timeout = aiohttp.ClientTimeout(total=4.5, connect=2.5)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        models_url, headers=headers, ssl=verify_ssl
                    ) as resp:
                        if not resp.ok:
                            logger.info(
                                "Model discovery HTTP %s for %s",
                                resp.status,
                                models_url,
                            )
                            return found
                        body = await resp.json()
                        for m in body.get("data", []) or []:
                            mid = m.get("id", m.get("name", ""))
                            if not mid:
                                continue
                            entry = {
                                "id": mid,
                                "name": mid,
                                "provider": configured_provider,
                                "default": False,
                                "source": "endpoint",
                            }
                            # RMB / local hosts advertise real n_ctx — cache for
                            # Memory Harness + local max_tokens budgeting.
                            ctx = (
                                m.get("context_window")
                                or m.get("context_length")
                                or m.get("max_model_len")
                                or m.get("n_ctx")
                            )
                            if ctx is None and isinstance(m.get("meta"), dict):
                                ctx = m["meta"].get("context_window")
                            if ctx is not None:
                                try:
                                    ctx_i = int(ctx)
                                    entry["context_window"] = ctx_i
                                    from remedy.nanoswarm.token_nanobot import (
                                        cache_context_window,
                                    )

                                    cache_context_window(base_url, mid, ctx_i)
                                except (TypeError, ValueError):
                                    pass
                            found.append(entry)
            except Exception as exc:
                logger.info(
                    "Model discovery failed for %s (%s): %s: %s",
                    configured_provider,
                    models_url,
                    type(exc).__name__,
                    exc or repr(exc),
                )
            return found

        async def _discover_anthropic() -> list[dict]:
            from remedy.interfaces.config import (
                anthropic_auth_headers,
                anthropic_models_url,
                parse_anthropic_models_payload,
            )

            models_url = anthropic_models_url(base_url)
            headers = anthropic_auth_headers(api_key)
            timeout = aiohttp.ClientTimeout(total=6.0, connect=3.0)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        models_url, headers=headers, ssl=verify_ssl
                    ) as resp:
                        if not resp.ok:
                            logger.info(
                                "Anthropic model discovery HTTP %s for %s",
                                resp.status,
                                models_url,
                            )
                            return []
                        body = await resp.json()
                        return parse_anthropic_models_payload(body)
            except Exception as exc:
                logger.info(
                    "Anthropic model discovery failed (%s): %s: %s",
                    models_url,
                    type(exc).__name__,
                    exc or repr(exc),
                )
                return []

        # OpenAI-compatible /models (DeepSeek, xAI, OpenAI, Ollama /v1, OpenRouter, Poe, …)
        if (
            not from_cache
            and want_live
            and can_live
            and openai_compat
            and base_url
        ):
            # Single-flight: parallel status-bar/boot fetches share one probe.
            import asyncio

            existing = inflight.get(cache_key)
            if existing is not None:
                try:
                    discovered = list(await existing)
                except Exception:
                    discovered = []
            else:
                loop = asyncio.get_event_loop()
                fut: asyncio.Future = loop.create_future()
                inflight[cache_key] = fut
                try:
                    result = await _discover_openai_compat()
                    if not fut.done():
                        fut.set_result(result)
                    discovered = list(result)
                except Exception as exc:
                    if not fut.done():
                        fut.set_result([])
                    discovered = []
                    logger.info(
                        "Model discovery inflight error for %s: %s: %s",
                        configured_provider,
                        type(exc).__name__,
                        exc or repr(exc),
                    )
                finally:
                    inflight.pop(cache_key, None)

        if (
            not from_cache
            and want_live
            and can_live
            and configured_provider == "anthropic"
            and api_key
        ):
            import asyncio

            existing = inflight.get(cache_key)
            if existing is not None:
                try:
                    discovered = list(await existing)
                except Exception:
                    discovered = []
            else:
                loop = asyncio.get_event_loop()
                fut = loop.create_future()
                inflight[cache_key] = fut
                try:
                    result = await _discover_anthropic()
                    if not fut.done():
                        fut.set_result(result)
                    discovered = list(result)
                except Exception as exc:
                    if not fut.done():
                        fut.set_result([])
                    discovered = []
                    logger.info(
                        "Anthropic discovery inflight error: %s: %s",
                        type(exc).__name__,
                        exc or repr(exc),
                    )
                finally:
                    inflight.pop(cache_key, None)

        # Ollama native tags API
        if not from_cache and (configured_provider == "ollama" or "11434" in (base_url or "")):
            try:
                ollama_url = base_url.rstrip("/").removesuffix("/v1") + "/api/tags"
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=3.0)
                ) as session:
                    async with session.get(ollama_url) as resp:
                        if resp.ok:
                            body = await resp.json()
                            for m in body.get("models", []):
                                mid = m.get("name", "")
                                if not mid:
                                    continue
                                name = mid.rstrip(":latest") if mid.endswith(":latest") else mid
                                if not any(d["id"] == name for d in discovered):
                                    discovered.append(
                                        {
                                            "id": name,
                                            "name": name,
                                            "provider": "ollama",
                                            "default": False,
                                            "source": "endpoint",
                                        }
                                    )
            except Exception as exc:
                logger.debug("Ollama discovery failed: %s", exc)

        if not from_cache:
            # Cache successes long; empty probes short so we retry soon.
            ok = bool(discovered)
            cache[cache_key] = (now, list(discovered), ok)

        # Merge: **live endpoint first**, then curated catalog as fallback/enrichment.
        # Demo: catalog-only allowlist (ignore gateway dump).
        if configured_provider == "demo":
            discovered = []
        seen: set[str] = set()
        merged: list[dict] = []
        # Prefer friendly catalog names when id matches a live discovery.
        catalog_names = {
            str(c.get("id")): str(c.get("name") or c.get("id"))
            for c in catalog
            if c.get("id")
        }
        for m in discovered + catalog:
            mid = str(m.get("id") or "")
            if not mid or mid in seen:
                continue
            # On closed providers, drop discovered ids that clearly belong elsewhere
            # (e.g. Claude id on DeepSeek). Trust ids that start with native prefixes.
            if configured_provider not in ("openrouter", "custom", "ollama", "demo", "poe"):
                from remedy.interfaces.config import (
                    _native_model_id_for_provider,
                    infer_provider_from_model,
                )

                owner = infer_provider_from_model(mid)
                if (
                    owner
                    and owner != configured_provider
                    and not _native_model_id_for_provider(configured_provider, mid)
                ):
                    continue
            # Demo: never list image/video/embedding or non-allowlisted gateway junk
            if configured_provider == "demo" and not _demo_model_allowed(mid, catalog):
                continue
            # Soft-hide pure media bots on multi-model gateways (Poe/xAI imagine)
            if configured_provider in ("poe", "xai", "openrouter") and _looks_like_media_model(
                mid
            ):
                continue
            seen.add(mid)
            display = catalog_names.get(mid) or m.get("name") or mid
            merged.append(
                {
                    "id": mid,
                    "name": display,
                    "provider": configured_provider,
                    "default": False,
                    "source": m.get("source") or ("catalog" if mid in catalog_names else "endpoint"),
                }
            )

        if not merged:
            merged = catalog_models_for_provider(configured_provider) or [
                {
                    "id": configured_id or "default",
                    "name": configured_id or "default",
                    "provider": configured_provider,
                    "default": True,
                }
            ]

        if configured_id and not any(m["id"] == configured_id for m in merged):
            # Demo: do not surface a leftover foreign id (e.g. deepseek-*) as the selection.
            if configured_provider != "demo" or _demo_model_allowed(
                configured_id, catalog
            ):
                merged.insert(
                    0,
                    {
                        "id": configured_id,
                        "name": configured_id,
                        "provider": configured_provider,
                        "default": True,
                    },
                )
            else:
                configured_id = (merged[0]["id"] if merged else "codestral-latest")

        default_id = configured_id or (merged[0]["id"] if merged else "default")
        for m in merged:
            m["default"] = m["id"] == default_id

        return {
            "models": merged,
            "default": default_id,
            "provider": configured_provider,
            "base_url": base_url,
        }

    @app.get("/api/agents")
    async def list_agents():
        return {"agents": _BUILTIN_AGENTS}

    # -- custom commands (markdown-based, ~/.remedy/commands/) ----------------
    @app.get("/api/commands/custom")
    async def list_custom_commands():
        cmd_dir = Path.home() / ".remedy" / "commands"
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
        cmd_dir = Path.home() / ".remedy" / "commands"
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
        agent_dir = Path.home() / ".remedy" / "agents"
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
        agent_dir = Path.home() / ".remedy" / "agents"
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

