"""LLM HTTP helpers + fallback text (extracted from BasicRuntime)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


def _sleev_force(runtime: Any) -> bool:
    try:
        from remedy.core.turn_context import turn_sleev_force_direct

        return bool(turn_sleev_force_direct(runtime))
    except Exception:
        return bool(getattr(runtime, "_sleev_force_direct", False))

# Shared session for LLM API calls — avoids per-call TLS handshake overhead.
#
# This module OWNS the one aiohttp session every LLM request path uses. The
# ReAct loop used to build its own ``ClientSession`` + ``TCPConnector`` per
# turn next to this one; with two pools in play a turn aborted mid-request
# left the per-turn pool half-closed ("Unclosed client session",
# ``ConnectionResetError`` at step boundaries). One owner, one close.
_shared_session: aiohttp.ClientSession | None = None
_shared_loop: asyncio.AbstractEventLoop | None = None

#: Pool shape shared by every request path. Per-request ``timeout=`` always
#: overrides the session default; the default is a generous ceiling so a
#: caller that forgets still cannot hang forever.
SESSION_CONNECTOR_LIMIT = 24
SESSION_DNS_TTL_S = 300
SESSION_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=3_600, sock_read=900, connect=60)


def get_shared_session() -> aiohttp.ClientSession:
    """Return the process-wide aiohttp session, creating it lazily.

    A session is bound to the loop that created it. When the loop changed
    (tests, a restarted server loop) the stale session is closed on its own
    loop where possible and a fresh one is built for the current loop.
    """
    global _shared_session, _shared_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    sess = _shared_session
    if sess is not None and not sess.closed and (loop is None or _shared_loop is loop):
        return sess
    if sess is not None and not sess.closed:
        _close_on_owner_loop(sess, _shared_loop)
    _shared_session = aiohttp.ClientSession(
        timeout=SESSION_DEFAULT_TIMEOUT,
        connector=aiohttp.TCPConnector(
            limit=SESSION_CONNECTOR_LIMIT,
            ttl_dns_cache=SESSION_DNS_TTL_S,
        ),
    )
    _shared_loop = loop
    return _shared_session


# Back-compat name for older call sites.
_get_shared_session = get_shared_session


def _close_on_owner_loop(
    sess: aiohttp.ClientSession, owner: asyncio.AbstractEventLoop | None
) -> None:
    """Close *sess* on the loop that owns it; detach the connector otherwise."""
    try:
        if owner is not None and not owner.is_closed():
            if owner.is_running():
                fut = asyncio.run_coroutine_threadsafe(sess.close(), owner)
                with contextlib.suppress(Exception):
                    fut.result(timeout=2.0)
                return
            owner.run_until_complete(sess.close())
            return
    except Exception:
        pass
    # Owner loop gone: the session cannot be closed cleanly; detach so the
    # connector does not try to finalize on a dead loop.
    with contextlib.suppress(Exception):
        sess.detach()


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
            total = 300.0
            # Parsed separately: this used to sit inside the same try, so a
            # malformed override ("300s", "") threw *after* we had decided the
            # model is local and left `total` on the 120s cloud wall — the one
            # case the longer wall exists for, broken by the very setting meant
            # to tune it.
            raw_override = os.environ.get("REMEDY_LOCAL_LLM_TIMEOUT")
            if raw_override:
                with contextlib.suppress(TypeError, ValueError):
                    total = float(raw_override)
    except Exception:
        pass
    return aiohttp.ClientTimeout(total=total, connect=30)


#: Close tasks we scheduled but cannot await, held so the loop does not collect
#: them mid-close ("Task was destroyed but it is pending").
_closing: set[asyncio.Task] = set()


async def aclose_shared_session() -> None:
    """Close the shared session. Await this on shutdown (API lifespan does)."""
    global _shared_session, _shared_loop
    session, _shared_session = _shared_session, None
    owner, _shared_loop = _shared_loop, None
    if session is None or session.closed:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if owner is not None and loop is not owner:
        _close_on_owner_loop(session, owner)
        return
    with contextlib.suppress(Exception):
        await session.close()


aclose = aclose_shared_session


def close_shared_session() -> None:
    """Close the shared session from sync code.

    The body of this used to be a bare ``pass``, under a comment saying
    ``ClientSession.close()`` is async and the caller should await it. No caller
    ever did — nothing in the tree imports this function — so the session behind
    every LLM call was only ever dereferenced, never closed, leaving its
    connection pool open until the interpreter went away. Worse, dropping the
    reference makes the next call build a *second* session, so anyone who did
    wire this up would have leaked one pool per shutdown.

    Prefer ``aclose_shared_session`` where you can await.
    """
    global _shared_session, _shared_loop
    session, _shared_session = _shared_session, None
    _shared_loop = None
    if session is None or session.closed:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop on this thread: spin one up just long enough to close.
        with contextlib.suppress(Exception):
            asyncio.run(session.close())
        return
    task = loop.create_task(session.close())
    _closing.add(task)
    task.add_done_callback(_closing.discard)


# Coding-first tool order when pre-sliming for local/RMB hosts (matches RMB priority).
_LOCAL_TOOL_PRIORITY = (
    "list_dir",
    "file_glob",
    "file_read",
    "file_write",
    "file_edit",
    "file_edit_batch",
    "host_run",
    "todo_write",
    "todo_read",
    "build_drive",
    "build_parallel",
    "apply_patch",
    "build_review_fix",
    "companion_context",
    "clipboard_read",
    "clipboard_write",
    "companion_design",
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
        # Only shapes the emit pass below can actually keep. Scoring used to
        # accept a flat {"name": ...} tool that emit then discarded, so it won
        # a high-priority slot and vanished — the returned list came back
        # shorter than max_tools with valid tools left behind.
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "")
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
        raw_params = fn.get("parameters")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        raw_props = params.get("properties")
        props_in: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
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
        raw_req = params.get("required")
        req: list[Any] = raw_req if isinstance(raw_req, list) else []
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
                    CODING_TOOL_PACK,
                    EXPAND_TOOL_PACK,
                    resolve_local_window,
                    slim_tools_pack,
                    tool_pack_for_window,
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
    *,
    step: int | None = None,
) -> dict[str, Any] | str:
    """POST chat completions; one xAI re-auth attempt on 401/403.

    Uses per-turn ``LlmBinding`` (ContextVar) so concurrent multi-provider
    turns do not share host/key/model. May update ``runtime._llm_api_key``
    and the turn binding after a successful OAuth refresh.

    Every outcome — success, HTTP error, abort, exception — emits one
    ``remedy.llm`` line (see :mod:`remedy.core.llm_log`).
    """
    import time as _time

    from remedy.core.llm_binding import get_llm_binding
    from remedy.core.llm_log import count_tool_calls, log_llm_call

    bind = get_llm_binding(runtime)
    sid = None
    with contextlib.suppress(Exception):
        from remedy.core.turn_context import turn_session_id

        sid = turn_session_id(runtime)
    t0 = _time.perf_counter()
    fields: dict[str, Any] = {
        "provider": bind.provider,
        "model": bind.model,
        "session_id": sid,
        "step": step,
    }
    try:
        result = await _post_chat_inner(runtime, body)
    except asyncio.CancelledError:
        log_llm_call(
            **fields, status="aborted", latency_ms=(_time.perf_counter() - t0) * 1000
        )
        raise
    except Exception as exc:
        log_llm_call(
            **fields,
            status="error",
            latency_ms=(_time.perf_counter() - t0) * 1000,
            error=exc,
        )
        raise
    latency = (_time.perf_counter() - t0) * 1000
    if isinstance(result, dict):
        adapter = None
        with contextlib.suppress(Exception):
            adapter = get_llm_binding(runtime).adapter()
        finish = None
        with contextlib.suppress(Exception):
            finish = adapter.extract_finish_reason(result) if adapter else None
        log_llm_call(
            **fields,
            status="ok",
            latency_ms=latency,
            finish_reason=finish,
            tool_calls=count_tool_calls(result, adapter),
            usage=result.get("usage"),
        )
    else:
        m = re.search(r"HTTP (\d{3})", str(result or ""))
        status = f"http_{m.group(1)}" if m else "error"
        if "[auth required]" in str(result or ""):
            status = "http_401"
        log_llm_call(**fields, status=status, latency_ms=latency)
    return result


async def _post_chat_inner(
    runtime: Any,
    body: dict[str, Any],
) -> dict[str, Any] | str:
    from remedy.core.llm_binding import LlmBinding, get_llm_binding, set_llm_binding
    from remedy.core.provider_sanitize import sanitize_chat_body

    bind = get_llm_binding(runtime)
    adapter = bind.adapter()
    headers = adapter.auth_headers(bind.api_key)
    endpoint = adapter.chat_endpoint(bind.base_url)
    try:
        from remedy.core.sleev import prepare_llm_http

        endpoint, headers = prepare_llm_http(
            provider=bind.provider,
            base_url=bind.base_url,
            api_key=bind.api_key,
            adapter=adapter,
            runtime=runtime,
            force_direct=_sleev_force(runtime),
        )
    except Exception:
        pass
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
    # ``async with`` on both posts: a CancelledError mid-body releases the
    # connection back to the pool instead of leaving it half-read.
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
                        try:
                            from remedy.core.sleev import prepare_llm_http

                            endpoint, headers = prepare_llm_http(
                                provider=bind.provider,
                                base_url=bind.base_url,
                                api_key=bind.api_key,
                                adapter=adapter,
                                runtime=runtime,
                                force_direct=_sleev_force(runtime),
                            )
                        except Exception:
                            pass
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
