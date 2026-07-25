"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import logging
import os
import time
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
    async def list_models():
        """List models for the *configured* provider only.

        Built-in catalogs are filtered by provider. Live discovery hits the
        configured base_url so DeepSeek never lists Claude, etc.
        """
        cfg = load_config()
        configured_provider = (
            (runtime._llm_provider if runtime is not None else None)
            or cfg.get("llm_provider")
            or os.environ.get("REMEDY_LLM_PROVIDER")
            or "openai"
        )
        configured_id = (
            (runtime._llm_model if runtime is not None else None)
            or cfg.get("llm_model")
            or os.environ.get("REMEDY_LLM_MODEL")
            or ""
        )
        base_url = (
            (runtime._llm_base_url if runtime is not None else None)
            or cfg.get("llm_base_url")
            or os.environ.get("REMEDY_LLM_BASE_URL")
            or ""
        )

        # Prefer live runtime / disk. Normalize is response-only (no GET writes).
        configured_provider = str(configured_provider or "openai").lower()
        configured_id = str(configured_id or "")
        base_url = str(base_url or "")
        # Soft-normalize for closed providers only when shaping default id display
        # — never persist from GET.
        _np, _nm, _nu = normalize_llm_settings(configured_provider, configured_id, base_url)
        if configured_provider not in ("openrouter", "custom", "ollama"):
            configured_provider, configured_id, base_url = _np, _nm, _nu
        elif not base_url:
            base_url = _nu
        if not configured_id:
            configured_id = _nm

        catalog = catalog_models_for_provider(configured_provider)
        api_key = ""
        if runtime is not None:
            api_key = getattr(runtime, "_llm_api_key", "") or ""
        if not api_key:
            api_key = str(cfg.get("llm_api_key") or os.environ.get("REMEDY_LLM_API_KEY") or "")

        # Discovery cache (process-local). Longer TTL — model lists rarely change mid-session.
        cache_key = f"{configured_provider}|{base_url}|{bool(api_key)}"
        cache = getattr(app.state, "_model_discovery_cache", None)
        if cache is None:
            app.state._model_discovery_cache = {}
            cache = app.state._model_discovery_cache
        now = time.time()
        cached = cache.get(cache_key)
        from_cache = bool(cached and (now - cached[0]) < 90)
        discovered = list(cached[1]) if from_cache else []

        verify_ssl = not _is_local_url(base_url)

        # Live GET {base_url}/models is the source of truth for OpenAI-compatible
        # providers (DeepSeek, xAI, OpenAI, Groq, …). Curated catalogs are fallback
        # only when discovery fails or returns empty.
        #
        # 0.10.44 briefly skipped cloud discovery for latency; that left users on
        # stale ids (e.g. deepseek-chat after V4 rename). Restored by default.
        # Opt out: REMEDY_LIVE_MODELS=0. Force on: REMEDY_LIVE_MODELS=1.
        live_env = str(os.environ.get("REMEDY_LIVE_MODELS", "")).strip().lower()
        live_off = live_env in ("0", "false", "no", "off")
        live_force = live_env in ("1", "true", "yes", "on")
        # Anthropic Messages API is not OpenAI /models compatible.
        openai_compat = configured_provider != "anthropic"
        want_live = (not live_off) and (
            live_force
            or openai_compat
            or _is_local_url(base_url)
            or configured_provider in ("ollama", "openrouter", "custom")
        )
        # Skip remote discovery without a key (would 401 and burn the timeout).
        can_live = bool(base_url) and (
            configured_provider == "ollama"
            or _is_local_url(base_url)
            or (api_key and api_key != "local")
            or live_force
        )

        # OpenAI-compatible /models (DeepSeek, xAI, OpenAI, Ollama /v1, OpenRouter, …)
        if (
            not from_cache
            and want_live
            and can_live
            and openai_compat
            and base_url
        ):
            try:
                # 3.5s: enough for cloud; cache (90s) keeps Settings/status bar snappy.
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=3.5)
                ) as session:
                    models_url = base_url.rstrip("/") + "/models"
                    headers: dict[str, str] = {}
                    if api_key and api_key != "local":
                        headers["Authorization"] = f"Bearer {api_key}"
                    async with session.get(models_url, headers=headers, ssl=verify_ssl) as resp:
                        if resp.ok:
                            body = await resp.json()
                            for m in body.get("data", []):
                                mid = m.get("id", m.get("name", ""))
                                if not mid:
                                    continue
                                discovered.append(
                                    {
                                        "id": mid,
                                        "name": mid,
                                        "provider": configured_provider,
                                        "default": False,
                                        "source": "endpoint",
                                    }
                                )
                        else:
                            logger.info(
                                "Model discovery HTTP %s for %s",
                                resp.status,
                                models_url,
                            )
            except Exception as exc:
                logger.info("Model discovery failed for %s: %s", base_url, exc)

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
                                        }
                                    )
            except Exception as exc:
                logger.debug("Ollama discovery failed: %s", exc)

        if not from_cache:
            cache[cache_key] = (now, list(discovered))

        # Merge: discovered first, then provider catalog only (never other providers).
        # For openrouter/custom keep full discovered set; for closed catalogs prefer
        # catalog + discovered but never inject foreign builtins.
        seen: set[str] = set()
        merged: list[dict] = []
        for m in discovered + catalog:
            mid = m["id"]
            if mid in seen:
                continue
            # On closed providers, drop discovered ids that clearly belong elsewhere
            if configured_provider not in ("openrouter", "custom", "ollama"):
                from remedy.interfaces.config import infer_provider_from_model

                owner = infer_provider_from_model(mid)
                if owner and owner != configured_provider:
                    continue
            seen.add(mid)
            merged.append(
                {
                    "id": mid,
                    "name": m.get("name", mid),
                    "provider": configured_provider,
                    "default": False,
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
            merged.insert(
                0,
                {
                    "id": configured_id,
                    "name": configured_id,
                    "provider": configured_provider,
                    "default": True,
                },
            )

        default_id = configured_id or merged[0]["id"]
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
                            name = fm.get("description", name)
                            desc = fm.get("description", "")
                    except Exception:
                        pass
            commands.append({"name": name, "description": desc, "file": str(f)})
        return {"commands": commands}

    @app.get("/api/commands/custom/{name}")
    async def get_custom_command(name: str):
        cmd_dir = Path.home() / ".remedy" / "commands"
        path = safe_path(cmd_dir, name + ".md")
        if not path or not path.exists():
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
        path = safe_path(agent_dir, name + ".md")
        if not path or not path.exists():
            raise HTTPException(404, f"Agent '{name}' not found")
        return {"content": path.read_text(encoding="utf-8", errors="replace")}

