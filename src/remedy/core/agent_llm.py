"""LLM HTTP helpers + fallback text (extracted from BasicRuntime)."""

from __future__ import annotations

import contextlib
import logging
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
        _shared_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60),
        )
    return _shared_session


def close_shared_session() -> None:
    """Close the shared session (call on shutdown)."""
    global _shared_session
    if _shared_session is not None and not _shared_session.closed:
        with contextlib.suppress(Exception):
            pass  # aiohttp.ClientSession.close() is async; caller should await
    _shared_session = None


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
    safe_body = sanitize_chat_body(body if isinstance(body, dict) else {})

    session = _get_shared_session()
    async with (
        session.post(
            endpoint,
            headers=headers,
            json=safe_body,
            timeout=aiohttp.ClientTimeout(total=60),
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
                            timeout=aiohttp.ClientTimeout(total=60),
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
