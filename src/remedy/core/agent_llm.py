"""LLM HTTP helpers + fallback text (extracted from BasicRuntime)."""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Shared session for LLM API calls — avoids per-call TLS handshake overhead.
_shared_session: aiohttp.ClientSession | None = None


def _get_shared_session() -> aiohttp.ClientSession:
    """Return a shared aiohttp session, creating it lazily if needed."""
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        # Default 120s: local RMB first tool-prime prefill can exceed 60s.
        _shared_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
        )
    return _shared_session


def _llm_timeout(bind: Any) -> aiohttp.ClientTimeout:
    """Longer wall for local/RMB (prefill + continuous tool chains)."""
    total = 120.0
    try:
        from remedy.nanoswarm.token_nanobot import is_local_model

        if is_local_model(
            getattr(bind, "provider", None),
            getattr(bind, "model", None),
            base_url=getattr(bind, "base_url", None),
        ):
            total = float(os.environ.get("REMEDY_LOCAL_LLM_TIMEOUT", "300"))
    except Exception:
        pass
    return aiohttp.ClientTimeout(total=total, connect=30)


def close_shared_session() -> None:
    """Close the shared session (call on shutdown)."""
    global _shared_session
    if _shared_session is not None and not _shared_session.closed:
        with contextlib.suppress(Exception):
            pass  # aiohttp.ClientSession.close() is async; caller should await
    _shared_session = None


# Coding-first tool order when pre-sliming for local/RMB hosts (matches RMB priority).
_LOCAL_TOOL_PRIORITY = (
    "list_dir",
    "file_read",
    "file_write",
    "file_edit",
    "file_edit_batch",
    "bash_exec",
    "run_python_file",
    "git_status",
    "git_push",
    "gh_release",
    "ship_status",
    "repo_search",
    "skill_search",
    "skill_activate",
    "skill_run",
    "skill_reload",
    "mission_start",
    "mission_status",
    "mission_verify",
    "mission_complete",
    "memory_search",
    "memory_write",
    "memory_list",
    "partner_remember",
    "partner_recall",
    "spread_run",
    "job_run",
    "job_status",
    "help_list",
    "help_read",
    "local_discover",
    "web_fetch",
    "web_search",
    "vision_decode",
    "comfyui",
)


def openai_tools_payload(tool_registry: Any) -> list[dict[str, Any]]:
    """Build OpenAI-style tools list from a ToolRegistry.

    Caches the payload on the registry keyed by schema generation so L1/L2
    turns do not re-walk every tool definition each request. Callers that
    strip tools for L1 still share the same cached all_tools list.
    """
    gen = int(getattr(tool_registry, "schema_generation", -1) or -1)
    cached = getattr(tool_registry, "_openai_payload_cache", None)
    cached_gen = int(getattr(tool_registry, "_openai_payload_gen", -2) or -2)
    if cached is not None and cached_gen == gen and gen >= 0:
        return cached

    tools: list[dict[str, Any]] = []
    for t in tool_registry.tools:
        params = t.parameters if t.parameters else {"type": "object", "properties": {}}
        if "type" not in params:
            params = {"type": "object", "properties": params}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or t.name,
                    "parameters": params,
                },
            }
        )
    try:
        tool_registry._openai_payload_cache = tools
        tool_registry._openai_payload_gen = gen
    except Exception:
        pass
    return tools


def slim_tools_for_local(
    tools: list[dict[str, Any]] | None,
    *,
    max_tools: int = 48,
    desc_chars: int = 200,
    max_props: int = 14,
) -> list[dict[str, Any]] | None:
    """Compact tool schemas for small local windows (RMB / llama.cpp / Ollama).

    Remedy registers 80+ tools with long OpenAPI descriptions. Local 7B hosts
    cannot prefill that; RMB also slims server-side, but client-side prioritization
    keeps coding tools first and shrinks wire size before HTTP.
    """
    if not tools:
        return tools
    max_tools = max(8, int(max_tools))
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for i, t in enumerate(tools):
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        name = str((fn or {}).get("name") or "")
        try:
            pri = _LOCAL_TOOL_PRIORITY.index(name)
        except ValueError:
            pri = 500 + i
        scored.append((pri, i, t))
    scored.sort(key=lambda x: (x[0], x[1]))

    out: list[dict[str, Any]] = []
    for _, _, t in scored[:max_tools]:
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        desc = str(fn.get("description") or name)[:desc_chars]
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
        props_in = (
            params.get("properties") if isinstance(params.get("properties"), dict) else {}
        )
        props: dict[str, Any] = {}
        for k, v in list(props_in.items())[:max_props]:
            if isinstance(v, dict):
                slim: dict[str, Any] = {"type": v.get("type") or "string"}
                if isinstance(v.get("enum"), list):
                    slim["enum"] = v["enum"][:16]
                pd = str(v.get("description") or "")
                if pd and len(pd) <= 100:
                    slim["description"] = pd
                props[str(k)] = slim
            else:
                props[str(k)] = {"type": "string"}
        req = params.get("required") if isinstance(params.get("required"), list) else []
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": [str(x) for x in req[:12]],
                    },
                },
            }
        )
    return out


def tools_for_binding(
    tools: list[dict[str, Any]] | None,
    runtime: Any | None = None,
) -> list[dict[str, Any]] | None:
    """Return tools, pre-slimmed when the current LLM binding is local/RMB.

    Window-aware: 8k hosts get a tight coding pack; 32k still slims aggressively
    so multi-step builds never spend half the window on tool JSON alone.
    """
    if not tools:
        return tools
    try:
        from remedy.core.llm_binding import get_llm_binding
        from remedy.nanoswarm.token_nanobot import is_local_model

        bind = get_llm_binding(runtime)
        if is_local_model(bind.provider, bind.model, base_url=bind.base_url):
            try:
                from remedy.core.endless_context import (
                    resolve_local_window,
                    slim_tools_pack,
                    tool_pack_for_window,
                    CODING_TOOL_PACK,
                    EXPAND_TOOL_PACK,
                )

                win = resolve_local_window(
                    provider=bind.provider,
                    model=bind.model,
                    base_url=bind.base_url,
                )
                max_tools, desc_chars, max_props = tool_pack_for_window(win)
                # Prefer coding tools first; expand if window allows
                names = list(CODING_TOOL_PACK) + list(EXPAND_TOOL_PACK)
                packed = slim_tools_pack(
                    tools,
                    names=tuple(names),
                    max_tools=max_tools,
                    desc_chars=desc_chars,
                    max_props=max_props,
                )
                if packed:
                    return packed
            except Exception:
                pass
            return slim_tools_for_local(tools)
    except Exception:
        pass
    return tools


async def post_chat(
    runtime: Any,
    body: dict[str, Any],
) -> dict[str, Any] | str:
    """POST chat completions; one xAI re-auth attempt on 401/403.

    Uses per-turn ``LlmBinding`` (ContextVar) so concurrent multi-provider
    turns do not share host/key/model. May update ``runtime._llm_api_key``
    and the turn binding after a successful OAuth refresh.
    """
    from remedy.core.llm_binding import LlmBinding, get_llm_binding, set_llm_binding
    from remedy.core.provider_sanitize import sanitize_chat_body

    bind = get_llm_binding(runtime)
    adapter = bind.adapter()
    headers = adapter.auth_headers(bind.api_key)
    endpoint = adapter.chat_endpoint(bind.base_url)
    _local = False
    try:
        from remedy.runtime.rmb.mode import is_rmb_provider

        _local = is_rmb_provider(
            bind.provider, getattr(bind, "base_url", None)
        ) or str(bind.provider or "").lower() in ("ollama", "llamacpp")
    except Exception:
        pass
    safe_body = sanitize_chat_body(
        body if isinstance(body, dict) else {},
        local_agent=_local,
    )

    session = _get_shared_session()
    timeout = _llm_timeout(bind)
    async with (
        session.post(
            endpoint,
            headers=headers,
            json=safe_body,
            timeout=timeout,
        ) as resp,
    ):
        if resp.status != 200:
            text = await resp.text()
            try:
                from remedy.core.metabolism.redact import redact_text

                safe_err = redact_text(text or "")
            except Exception:
                safe_err = "[redacted provider error]"
            # One refresh attempt for expired xAI OAuth tokens.
            if (
                resp.status in (401, 403)
                and str(bind.provider or "").lower() == "xai"
            ):
                try:
                    from remedy.interfaces.xai_auth import (
                        refresh_if_needed,
                        resolve_bearer,
                    )

                    home = None
                    if getattr(runtime, "config", None) is not None:
                        hd = getattr(runtime.config, "home_dir", None)
                        if hd:
                            home = Path(hd).expanduser()
                    refresh_if_needed(home)
                    new_token = resolve_bearer(home)
                    if new_token and new_token != bind.api_key:
                        runtime._llm_api_key = new_token
                        bind = LlmBinding(
                            provider=bind.provider,
                            model=bind.model,
                            base_url=bind.base_url,
                            api_key=new_token,
                        )
                        set_llm_binding(bind)
                        adapter = bind.adapter()
                        headers = adapter.auth_headers(bind.api_key)
                        async with session.post(
                            endpoint,
                            headers=headers,
                            json=safe_body,
                            timeout=timeout,
                        ) as resp2:
                            if resp2.status == 200:
                                return await resp2.json()
                            text = await resp2.text()
                            try:
                                from remedy.core.metabolism.redact import (
                                    redact_text as _rt,
                                )

                                safe_err = _rt(text or "")
                            except Exception:
                                safe_err = "[redacted provider error]"
                            logger.error(
                                "LLM API error %d after reauth: %s",
                                resp2.status,
                                safe_err[:500],
                            )
                            return (
                                "\n[auth required] xAI session expired. "
                                "Sign in again (Settings or `remedy auth login xai`).\n"
                            )
                except Exception as auth_exc:
                    logger.debug("xAI re-auth in post_chat failed: %s", auth_exc)
            logger.error("LLM API error %d: %s", resp.status, safe_err[:500])
            return (
                f"\n[LLM ERROR — HTTP {resp.status}]\n{safe_err[:500]}\n[END LLM ERROR]"
            )
        return await resp.json()


def fallback_response(runtime: Any, message: str) -> str:
    """No-LLM / offline stub answers."""
    msg_lower = message.lower().strip()
    name = getattr(getattr(runtime, "config", None), "name", None) or "Remedy"

    greetings = {"hello", "hi", "hey", "greetings", "yo"}
    words = set(msg_lower.rstrip("!.,?").split())
    if msg_lower in greetings or words & greetings:
        return f"Hello! I'm {name}. How can I help you?"

    if "help" in msg_lower or "?" in msg_lower:
        return (
            "I'm a basic agent runtime. I can remember conversations in my "
            "persistent store. Try using memory commands or tools if available."
        )

    if "remember" in msg_lower or "memory" in msg_lower:
        return (
            "I've stored our conversation in memory. "
            "I can recall it later if needed."
        )

    return (
        f"Received: {message[:200]}. "
        f"I'm running in fallback mode. Set an LLM API key (via config or "
        f"REMEDY_LLM_API_KEY env var) for intelligent responses."
    )
