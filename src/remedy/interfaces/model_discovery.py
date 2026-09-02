"""Generic LLM endpoint model discovery.

Given a base URL (and maybe a key), work out which API flavour the host
speaks, list its models, and pull whatever capability metadata the endpoint
exposes (context window, modalities, tool support, pricing, loaded state).

Flavours probed, in an order chosen from the provider hint / URL shape:

- ``ollama``     GET {root}/api/tags  (+ /api/show per model, /api/ps)
- ``openai``     GET {base}/models    (OpenAI, DeepSeek, xAI, Groq, Mistral,
                 OpenRouter, Poe, llm7, LM Studio, llama.cpp, vLLM, RMB, …)
                 enriched with LM Studio /api/v0/models, llama.cpp /props,
                 xAI /language-models when those hosts are detected
- ``anthropic``  GET {base}/models with x-api-key (paged)
- ``gemini``     GET {root}/models?key= (native Gemini listing)

Every probe is a soft failure: the result says what was attempted, whether it
worked, the HTTP status and an error string so callers (and the UI) can show
*why* a picker fell back to the curated catalog instead of hiding it.

Nothing here touches disk. A process-wide "known live models" registry lets
validators accept any id the provider's own endpoint returned.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

# Ids that are never chat models, whatever the host says (fallback heuristic
# when the endpoint does not describe modalities).
_NON_CHAT_SUBSTR = (
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
    "embed",
    "moderation",
    "rerank",
    "transcribe",
    "realtime",
    "audio",
    "bge-",
    "nomic-embed",
    "mxbai-embed",
    "all-minilm",
)

_MAX_OLLAMA_SHOW = 24
_DEFAULT_TIMEOUT = 4.5
_CONNECT_TIMEOUT = 2.5
# Skip the 2.5s aiohttp connect wait when 127.0.0.1:8787 is closed.
# Tests that fake HTTP against localhost disable this.
_PRECHECK_LOCAL_LISTEN = True
_LOCAL_LISTEN_TIMEOUT_S = 0.15

# Chrome polls GET /api/providers/connected often. A 1.5s urllib timeout on a
# firewalled Windows loopback (SYN dropped, not RST) froze the asyncio loop
# and made settings/presence look just as slow. Fail-fast TCP + short TTL.
_OLLAMA_DETECT_TTL_S = 3.0
_ollama_detect_cache: dict[str, Any] = {"key": None, "ts": 0.0, "value": None}
_ollama_detect_lock = threading.Lock()

# provider -> {model id -> row} for ids a live endpoint actually returned.
_LIVE_KNOWN: dict[str, dict[str, dict[str, Any]]] = {}
_LIVE_KNOWN_AT: dict[str, float] = {}
_LIVE_KNOWN_TTL = 6 * 3600.0


@dataclass
class DiscoveryResult:
    attempted: bool = False
    ok: bool = False
    status: int | None = None
    error: str | None = None
    url: str = ""
    flavour: str | None = None
    cached: bool = False
    models: list[dict[str, Any]] = field(default_factory=list)
    #: Model ids the host reports as currently loaded (Ollama /api/ps, LM Studio).
    loaded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "ok": self.ok,
            "status": self.status,
            "error": self.error,
            "url": self.url,
            "flavour": self.flavour,
            "cached": self.cached,
        }


# --------------------------------------------------------------------------
# URL helpers


def _root(base_url: str) -> str:
    """Strip a trailing /v1, /v1beta, /openai, /api segment chain → host root."""
    u = (base_url or "").strip().rstrip("/")
    for _ in range(3):
        for suf in ("/v1", "/v1beta", "/openai", "/api", "/chat/completions", "/messages"):
            if u.lower().endswith(suf):
                u = u[: -len(suf)]
                break
        else:
            break
    return u


def _parse_host_port(base_url: str) -> tuple[str, int] | None:
    """host, port for a URL, or None if unparseable."""
    try:
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        host = parsed.hostname or "127.0.0.1"
        if parsed.port:
            port = int(parsed.port)
        else:
            port = 443 if (parsed.scheme or "").lower() == "https" else 80
        return host, port
    except Exception:
        return None


# host:port -> (monotonic_ts, listening). Shared by async discovery and the
# sync Ollama detector so a closed 8787 is not probed twice in one chrome tick.
_LISTEN_TTL_S = 3.0
_listen_cache: dict[str, tuple[float, bool]] = {}
_listen_lock = threading.Lock()
_listen_inflight: dict[str, threading.Event] = {}


def _listen_cache_key(base_url: str) -> str | None:
    parsed = _parse_host_port(base_url)
    if parsed is None:
        return None
    host, port = parsed
    return f"{host}:{port}"


def _connect_local(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _local_host_listening_sync(
    base_url: str, timeout_s: float = _LOCAL_LISTEN_TIMEOUT_S
) -> bool:
    """True when a TCP connect to the local host:port succeeds quickly.

    Single-flight + 3s cache so concurrent GET /api/models (session + Settings)
    share one 150ms fail-fast instead of stacking asyncio.open_connection waits
    on the event loop (Windows Proactor + dropped SYN was the 780ms SLOW GET).
    """
    key = _listen_cache_key(base_url)
    if key is None:
        return True
    now = time.monotonic()
    with _listen_lock:
        hit = _listen_cache.get(key)
        if hit is not None and (now - hit[0]) < _LISTEN_TTL_S:
            return hit[1]
        waiter = _listen_inflight.get(key)
        leader = waiter is None
        if leader:
            waiter = threading.Event()
            _listen_inflight[key] = waiter
    assert waiter is not None
    if not leader:
        waiter.wait(timeout_s + 0.05)
        with _listen_lock:
            hit = _listen_cache.get(key)
            if hit is not None:
                return hit[1]
        return False
    try:
        host, port = key.rsplit(":", 1)
        ok = _connect_local(host, int(port), timeout_s)
        with _listen_lock:
            _listen_cache[key] = (time.monotonic(), ok)
        return ok
    finally:
        with _listen_lock:
            _listen_inflight.pop(key, None)
        waiter.set()


async def _local_host_listening(base_url: str, timeout_s: float = _LOCAL_LISTEN_TIMEOUT_S) -> bool:
    """True when a TCP connect to the local host:port succeeds quickly."""
    # socket.create_connection in a worker — not asyncio.open_connection.
    # Proactor ConnectEx + wait_for on a firewalled loopback can outlive the
    # 150ms budget and freeze sibling /api/models on the same loop.
    return await asyncio.to_thread(_local_host_listening_sync, base_url, timeout_s)


def _is_local_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "host.docker.internal") or (
        host.startswith("192.168.") or host.startswith("10.") or host.endswith(".local")
    )


def _port(url: str) -> int | None:
    try:
        return urlparse(url).port
    except Exception:
        return None


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def ollama_base_url_from_env() -> str | None:
    """``OLLAMA_HOST`` in any of the shapes Ollama accepts → ``http://host:port/v1``."""
    raw = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    p = urlparse(raw)
    host = p.hostname or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = p.port or 11434
    scheme = p.scheme or "http"
    return f"{scheme}://{host}:{port}/v1"


def looks_like_chat_model(mid: str) -> bool:
    """Heuristic used only when the endpoint does not describe modalities."""
    low = (mid or "").lower()
    return not any(s in low for s in _NON_CHAT_SUBSTR)


# --------------------------------------------------------------------------
# Row shaping


def _row(
    mid: str,
    *,
    name: str | None = None,
    context_window: Any = None,
    vision: bool | None = None,
    tools: bool | None = None,
    reasoning: bool | None = None,
    chat: bool | None = None,
    pricing: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": mid,
        "name": (name or mid).strip() or mid,
        "source": "endpoint",
    }
    ctx = _int_or_none(context_window)
    if ctx:
        row["context_window"] = ctx
    if vision is not None:
        row["vision"] = bool(vision)
    if tools is not None:
        row["tools"] = bool(tools)
    if reasoning is not None:
        row["reasoning"] = bool(reasoning)
    row["chat"] = looks_like_chat_model(mid) if chat is None else bool(chat)
    if pricing:
        row["pricing"] = pricing
    if extra:
        row.update(extra)
    return row


def _int_or_none(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        i = int(float(v))
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _openai_row(m: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one entry of an OpenAI-style ``data[]`` list.

    Reads the capability fields that OpenRouter, Mistral, Groq, LM Studio,
    llama.cpp, vLLM, RMB and Ollama's bridge put on their rows.
    """
    mid = str(m.get("id") or m.get("name") or m.get("model") or "").strip()
    if not mid:
        return None
    meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
    _arch_raw = m.get("architecture")
    arch = _arch_raw if isinstance(_arch_raw, dict) else {}
    caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}

    ctx = _first(
        m,
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "n_ctx",
        "max_input_tokens",
    )
    if ctx is None and meta:
        ctx = _first(meta, "context_window", "n_ctx_train", "n_ctx", "context_length")

    vision: bool | None = None
    tools: bool | None = None
    reasoning: bool | None = None
    chat: bool | None = None

    _in_raw = arch.get("input_modalities")
    _out_raw = arch.get("output_modalities")
    in_mods = _in_raw if isinstance(_in_raw, list) else None
    out_mods = _out_raw if isinstance(_out_raw, list) else None
    if in_mods is None and isinstance(m.get("input_modalities"), list):
        in_mods = m["input_modalities"]
    if out_mods is None and isinstance(m.get("output_modalities"), list):
        out_mods = m["output_modalities"]
    if in_mods is not None:
        vision = "image" in [str(x).lower() for x in in_mods]
    if out_mods is not None:
        chat = "text" in [str(x).lower() for x in out_mods]
    elif in_mods is not None:
        chat = "text" in [str(x).lower() for x in in_mods]

    sp = m.get("supported_parameters")
    if isinstance(sp, list):
        sp_l = [str(x).lower() for x in sp]
        tools = "tools" in sp_l or "tool_choice" in sp_l
        reasoning = "reasoning" in sp_l or "include_reasoning" in sp_l

    if caps:
        if "vision" in caps:
            vision = bool(caps.get("vision"))
        if "function_calling" in caps:
            tools = bool(caps.get("function_calling"))
        if "completion_chat" in caps:
            chat = bool(caps.get("completion_chat"))
        if "tools" in caps:
            tools = bool(caps.get("tools"))

    # LM Studio /api/v0/models: type llm|vlm|embeddings, state loaded|not-loaded
    mtype = str(m.get("type") or "").lower()
    if mtype in ("llm", "vlm"):
        chat = True
        if mtype == "vlm":
            vision = True
    elif mtype in ("embeddings", "embedding"):
        chat = False

    # Groq: active flag
    if m.get("active") is False:
        chat = False

    pricing = None
    pr = m.get("pricing")
    if isinstance(pr, dict) and pr:
        pricing = {k: pr[k] for k in ("prompt", "completion", "image", "request") if k in pr}

    name = str(m.get("display_name") or m.get("name") or "").strip() or None
    if name and name == mid:
        name = None
    extra: dict[str, Any] = {}
    if m.get("state") in ("loaded", "not-loaded"):
        extra["loaded"] = m.get("state") == "loaded"
    owned = m.get("owned_by")
    if owned:
        extra["owned_by"] = str(owned)
    return _row(
        mid,
        name=name,
        context_window=ctx,
        vision=vision,
        tools=tools,
        reasoning=reasoning,
        chat=chat,
        pricing=pricing,
        extra=extra,
    )


# --------------------------------------------------------------------------
# HTTP


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    verify_ssl: bool = True,
) -> tuple[int | None, Any, str | None]:
    """GET → (status, json-or-None, error). Never raises."""
    try:
        async with session.get(url, headers=headers or {}, ssl=verify_ssl) as resp:
            status = int(resp.status)
            if not resp.ok:
                detail = ""
                try:
                    body = await resp.json(content_type=None)
                    if isinstance(body, dict):
                        err = body.get("error")
                        if isinstance(err, dict):
                            detail = str(err.get("message") or "")
                        elif err:
                            detail = str(err)
                        if not detail and body.get("message"):
                            detail = str(body.get("message"))
                except Exception:
                    detail = ""
                if status in (401, 403):
                    msg = detail or "key rejected"
                elif status == 404:
                    msg = detail or "no model listing at this URL"
                else:
                    msg = detail or f"HTTP {status}"
                return status, None, msg
            try:
                return status, await resp.json(content_type=None), None
            except Exception as exc:
                return status, None, f"bad JSON: {type(exc).__name__}"
    except TimeoutError:
        return None, None, "timed out"
    except aiohttp.ClientConnectorError as exc:
        return None, None, f"connection failed: {exc.os_error or exc}"
    except aiohttp.ClientError as exc:
        return None, None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        return None, None, f"{type(exc).__name__}: {exc}"


def _bearer(api_key: str) -> dict[str, str]:
    key = (api_key or "").strip()
    if key and key not in ("local", "unused", "none"):
        return {"Authorization": f"Bearer {key}"}
    return {}


# --------------------------------------------------------------------------
# Flavour probes


async def _probe_openai(
    session: aiohttp.ClientSession, base_url: str, api_key: str, verify_ssl: bool
) -> DiscoveryResult:
    url = base_url.rstrip("/") + "/models"
    res = DiscoveryResult(attempted=True, url=url, flavour="openai")
    status, body, err = await _get_json(session, url, headers=_bearer(api_key), verify_ssl=verify_ssl)
    res.status = status
    if body is None:
        res.error = err
        return res
    items: list[Any]
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(body.get("models"), list):
            items = body["models"]
        else:
            items = []
    elif isinstance(body, list):
        items = body
    else:
        items = []
    seen: set[str] = set()
    for m in items:
        if isinstance(m, str):
            m = {"id": m}
        if not isinstance(m, dict):
            continue
        row = _openai_row(m)
        if row and row["id"] not in seen:
            seen.add(row["id"])
            res.models.append(row)
    res.ok = True
    return res


async def _probe_anthropic(
    session: aiohttp.ClientSession, base_url: str, api_key: str, verify_ssl: bool
) -> DiscoveryResult:
    from remedy.interfaces.config import anthropic_auth_headers, anthropic_models_url

    first_url = anthropic_models_url(base_url)
    res = DiscoveryResult(attempted=True, url=first_url, flavour="anthropic")
    headers = anthropic_auth_headers(api_key)
    after: str | None = None
    seen: set[str] = set()
    for _page in range(10):
        url = first_url + "?limit=1000" + (f"&after_id={after}" if after else "")
        status, body, err = await _get_json(session, url, headers=headers, verify_ssl=verify_ssl)
        res.status = status
        if body is None or not isinstance(body, dict):
            if not res.models:
                res.error = err or "bad payload"
                return res
            break
        for m in body.get("data") or []:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            res.models.append(
                _row(
                    mid,
                    name=str(m.get("display_name") or "") or None,
                    chat=True,
                    tools=True,
                    vision=True,
                    extra={"created_at": m.get("created_at")} if m.get("created_at") else None,
                )
            )
        if body.get("has_more") and body.get("last_id"):
            after = str(body["last_id"])
            continue
        break
    res.ok = True
    return res


async def _probe_ollama(
    session: aiohttp.ClientSession, base_url: str, api_key: str, verify_ssl: bool
) -> DiscoveryResult:
    root = _root(base_url)
    url = root + "/api/tags"
    res = DiscoveryResult(attempted=True, url=url, flavour="ollama")
    headers = _bearer(api_key)
    status, body, err = await _get_json(session, url, headers=headers, verify_ssl=verify_ssl)
    res.status = status
    if body is None or not isinstance(body, dict) or not isinstance(body.get("models"), list):
        res.error = err or "not an Ollama host"
        return res
    names: list[str] = []
    sizes: dict[str, int] = {}
    families: dict[str, str] = {}
    for m in body["models"]:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or m.get("model") or "").strip()
        if not name:
            continue
        short = name.removesuffix(":latest")
        if short in names:
            continue
        names.append(short)
        _details = m.get("details")
        details = _details if isinstance(_details, dict) else {}
        family = details.get("family")
        if family:
            families[short] = str(family)
        sz = _int_or_none(m.get("size"))
        if sz:
            sizes[short] = sz
    res.ok = True

    # Loaded models
    status_ps, ps, _e = await _get_json(session, root + "/api/ps", headers=headers, verify_ssl=verify_ssl)
    if isinstance(ps, dict) and isinstance(ps.get("models"), list):
        for m in ps["models"]:
            if isinstance(m, dict):
                n = str(m.get("name") or m.get("model") or "").removesuffix(":latest")
                if n:
                    res.loaded.append(n)

    # Per-model capabilities / context (bounded)
    async def _show(short: str) -> dict[str, Any]:
        try:
            async with session.post(
                root + "/api/show",
                json={"model": short},
                headers=headers,
                ssl=verify_ssl,
            ) as resp:
                if not resp.ok:
                    return {}
                return await resp.json(content_type=None)
        except Exception:
            return {}

    shows: list[dict[str, Any]] = []
    subset = names[:_MAX_OLLAMA_SHOW]
    if subset:
        shows = list(await asyncio.gather(*[_show(n) for n in subset], return_exceptions=False))
    show_by = dict(zip(subset, shows, strict=False))

    for short in names:
        info = show_by.get(short) or {}
        _caps = info.get("capabilities")
        caps = _caps if isinstance(_caps, list) else []
        caps_l = [str(c).lower() for c in caps]
        ctx = None
        _mi = info.get("model_info")
        mi = _mi if isinstance(_mi, dict) else {}
        for k, v in mi.items():
            if str(k).endswith(".context_length"):
                ctx = v
                break
        chat: bool | None = None
        if caps_l:
            chat = "completion" in caps_l and "embedding" not in caps_l
        fam = families.get(short, "")
        if chat is None and fam:
            chat = "bert" not in fam.lower() and "nomic" not in fam.lower()
        res.models.append(
            _row(
                short,
                context_window=ctx,
                vision=("vision" in caps_l) if caps_l else None,
                tools=("tools" in caps_l) if caps_l else None,
                reasoning=("thinking" in caps_l) if caps_l else None,
                chat=chat,
                extra={
                    "loaded": short in res.loaded,
                    **({"size_bytes": sizes[short]} if short in sizes else {}),
                    **({"family": fam} if fam else {}),
                },
            )
        )
    return res


async def _probe_gemini(
    session: aiohttp.ClientSession, base_url: str, api_key: str, verify_ssl: bool
) -> DiscoveryResult:
    root = _root(base_url)
    url = root + "/v1beta/models"
    res = DiscoveryResult(attempted=True, url=url, flavour="gemini")
    key = (api_key or "").strip()
    if not key:
        res.error = "no key"
        return res
    token: str | None = None
    seen: set[str] = set()
    for _page in range(10):
        page_url = f"{url}?key={key}&pageSize=200" + (f"&pageToken={token}" if token else "")
        status, body, err = await _get_json(session, page_url, verify_ssl=verify_ssl)
        res.status = status
        if body is None or not isinstance(body, dict):
            if not res.models:
                res.error = err or "bad payload"
                return res
            break
        for m in body.get("models") or []:
            if not isinstance(m, dict):
                continue
            full = str(m.get("name") or "").strip()
            mid = full.removeprefix("models/")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            methods = [str(x) for x in (m.get("supportedGenerationMethods") or [])]
            chat = "generateContent" in methods
            res.models.append(
                _row(
                    mid,
                    name=str(m.get("displayName") or "") or None,
                    context_window=m.get("inputTokenLimit"),
                    chat=chat,
                    tools=chat or None,
                    extra={"output_token_limit": m.get("outputTokenLimit")}
                    if m.get("outputTokenLimit")
                    else None,
                )
            )
        token = body.get("nextPageToken")
        if not token:
            break
    res.ok = True
    return res


# --------------------------------------------------------------------------
# Host-specific enrichment after an OpenAI-style listing


async def _enrich_lmstudio(
    session: aiohttp.ClientSession, base_url: str, res: DiscoveryResult, verify_ssl: bool
) -> None:
    root = _root(base_url)
    _s, body, _e = await _get_json(session, root + "/api/v0/models", verify_ssl=verify_ssl)
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return
    by_id = {r["id"]: r for r in res.models}
    for m in body["data"]:
        if not isinstance(m, dict):
            continue
        row = _openai_row(m)
        if not row:
            continue
        cur = by_id.get(row["id"])
        if cur is None:
            res.models.append(row)
            by_id[row["id"]] = row
        else:
            for k, v in row.items():
                if k not in cur or cur[k] in (None, "") or k in ("chat", "vision", "context_window", "loaded"):
                    cur[k] = v
        if row.get("loaded"):
            res.loaded.append(row["id"])
    res.flavour = "lmstudio"


async def _enrich_llamacpp(
    session: aiohttp.ClientSession, base_url: str, res: DiscoveryResult, verify_ssl: bool
) -> None:
    root = _root(base_url)
    _s, body, _e = await _get_json(session, root + "/props", verify_ssl=verify_ssl)
    if not isinstance(body, dict):
        return
    dgs = body.get("default_generation_settings")
    n_ctx = _int_or_none(dgs.get("n_ctx")) if isinstance(dgs, dict) else None
    for r in res.models:
        if n_ctx and not r.get("context_window"):
            r["context_window"] = n_ctx
        r["chat"] = True
        r["loaded"] = True
    res.flavour = "llamacpp"


def _apply_xai_language_models(res: DiscoveryResult, body: Any) -> None:
    """Merge xAI /language-models into an OpenAI-style listing. Same rows, plus aliases."""
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        return
    by_id = {r["id"]: r for r in res.models}
    for m in body["models"]:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        cur = by_id.get(mid)
        if cur is None:
            continue
        in_mods = [str(x).lower() for x in (m.get("input_modalities") or [])]
        out_mods = [str(x).lower() for x in (m.get("output_modalities") or [])]
        if in_mods:
            cur["vision"] = "image" in in_mods
        if out_mods:
            cur["chat"] = "text" in out_mods
        pricing = {}
        for k_src, k_dst in (
            ("prompt_text_token_price", "prompt"),
            ("completion_text_token_price", "completion"),
        ):
            if m.get(k_src) is not None:
                pricing[k_dst] = m[k_src]
        if pricing:
            cur["pricing"] = pricing
        for alias in m.get("aliases") or []:
            a = str(alias)
            if a and a not in by_id:
                res.models.append(_row(a, chat=cur.get("chat"), vision=cur.get("vision")))
                by_id[a] = res.models[-1]


async def _enrich_xai(
    session: aiohttp.ClientSession, base_url: str, api_key: str, res: DiscoveryResult, verify_ssl: bool
) -> None:
    url = base_url.rstrip("/") + "/language-models"
    _s, body, _e = await _get_json(session, url, headers=_bearer(api_key), verify_ssl=verify_ssl)
    _apply_xai_language_models(res, body)


# --------------------------------------------------------------------------
# Public API


def _flavour_order(provider_hint: str | None, base_url: str) -> list[str]:
    hint = (provider_hint or "").strip().lower()
    port = _port(base_url)
    host = _host(base_url)
    if hint == "anthropic" or "anthropic.com" in host:
        return ["anthropic", "openai"]
    if hint == "ollama" or port == 11434:
        return ["ollama", "openai"]
    if hint == "google" or "generativelanguage" in host:
        return ["openai", "gemini"]
    if hint in ("openai", "deepseek", "xai", "groq", "mistral", "openrouter", "poe", "demo", "rmb", "llamacpp"):
        return ["openai"]
    # custom / unknown: OpenAI-style first, then the others if local.
    order = ["openai"]
    if _is_local_url(base_url):
        order += ["ollama"]
    order += ["anthropic"]
    return order


async def discover_models(
    base_url: str,
    api_key: str = "",
    *,
    provider_hint: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    verify_ssl: bool | None = None,
) -> DiscoveryResult:
    """Probe *base_url* and return what it serves. Never raises."""
    base_url = (base_url or "").strip()
    if not base_url:
        return DiscoveryResult(attempted=False, error="no base URL")
    if verify_ssl is None:
        verify_ssl = not _is_local_url(base_url)
    if (
        _PRECHECK_LOCAL_LISTEN
        and _is_local_url(base_url)
        and not await _local_host_listening(base_url)
    ):
        return DiscoveryResult(
            attempted=True,
            url=base_url,
            error="not listening",
        )
    order = _flavour_order(provider_hint, base_url)
    hint = (provider_hint or "").strip().lower()
    last: DiscoveryResult | None = None
    client_timeout = aiohttp.ClientTimeout(total=timeout, connect=min(_CONNECT_TIMEOUT, timeout))
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            # First live xAI listing was two RTTs (/models then /language-models).
            # Fire both; merge aliases/modalities without dropping ids.
            xai_lang_task: asyncio.Task[tuple[int | None, Any, str | None]] | None = None
            if hint == "xai":
                xai_lang_task = asyncio.create_task(
                    _get_json(
                        session,
                        base_url.rstrip("/") + "/language-models",
                        headers=_bearer(api_key),
                        verify_ssl=verify_ssl,
                    )
                )
            try:
                for flavour in order:
                    if flavour == "ollama":
                        res = await _probe_ollama(session, base_url, api_key, verify_ssl)
                    elif flavour == "anthropic":
                        res = await _probe_anthropic(session, base_url, api_key, verify_ssl)
                    elif flavour == "gemini":
                        res = await _probe_gemini(session, base_url, api_key, verify_ssl)
                    else:
                        res = await _probe_openai(session, base_url, api_key, verify_ssl)
                    if res.ok and not res.models:
                        # A listing with nothing in it is not a usable answer —
                        # short cache TTL and a message the UI can show.
                        res.ok = False
                        res.error = res.error or "endpoint listed no models"
                    if res.ok:
                        if flavour == "openai":
                            if xai_lang_task is not None:
                                _s, body, _e = await xai_lang_task
                                xai_lang_task = None
                                _apply_xai_language_models(res, body)
                            else:
                                await _enrich_openai_host(
                                    session, base_url, api_key, hint, res, verify_ssl
                                )
                        # Google: the OpenAI bridge lists ids only; native listing
                        # adds token limits + generation methods when reachable.
                        if flavour == "openai" and "gemini" in order:
                            native = await _probe_gemini(session, base_url, api_key, verify_ssl)
                            if native.ok and native.models:
                                _merge_rows(res, native.models)
                        _remember(hint, res)
                        return res
                    # Auth failures are authoritative — do not try other flavours
                    # with the same key and mask the real reason.
                    if res.status in (401, 403) and last is None and len(order) > 1 and hint:
                        return res
                    last = res if (last is None or res.status is not None) else last
            finally:
                if xai_lang_task is not None:
                    xai_lang_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await xai_lang_task
    except Exception as exc:  # pragma: no cover - session construction
        return DiscoveryResult(attempted=True, url=base_url, error=f"{type(exc).__name__}: {exc}")
    return last or DiscoveryResult(attempted=True, url=base_url, error="no probe ran")


async def _enrich_openai_host(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    hint: str,
    res: DiscoveryResult,
    verify_ssl: bool,
) -> None:
    if hint == "xai":
        await _enrich_xai(session, base_url, api_key, res, verify_ssl)
        return
    if not _is_local_url(base_url):
        return
    # llama.cpp rows carry meta.n_ctx_train; LM Studio has /api/v0/models.
    looks_llamacpp = any("n_ctx_train" in str(r) for r in res.models) or hint == "llamacpp"
    if looks_llamacpp or _port(base_url) == 8080:
        await _enrich_llamacpp(session, base_url, res, verify_ssl)
        if res.flavour == "llamacpp":
            return
    if hint in ("custom", "lmstudio", "") or _port(base_url) == 1234:
        await _enrich_lmstudio(session, base_url, res, verify_ssl)


def _merge_rows(res: DiscoveryResult, rows: list[dict[str, Any]]) -> None:
    by_id = {r["id"]: r for r in res.models}
    for row in rows:
        cur = by_id.get(row["id"])
        if cur is None:
            res.models.append(row)
            by_id[row["id"]] = row
            continue
        for k, v in row.items():
            if v is None:
                continue
            if k not in cur or cur[k] in (None, ""):
                cur[k] = v
            elif k in ("chat", "vision", "tools", "context_window", "name"):
                cur[k] = v


def _remember(provider: str, res: DiscoveryResult) -> None:
    if not provider or not res.ok:
        return
    _LIVE_KNOWN[provider] = {r["id"]: r for r in res.models}
    _LIVE_KNOWN_AT[provider] = time.time()


def live_known_models(provider: str | None) -> dict[str, dict[str, Any]]:
    """Ids (→ rows) the provider's endpoint returned recently; {} if unknown."""
    prov = (provider or "").strip().lower()
    at = _LIVE_KNOWN_AT.get(prov)
    if not at or (time.time() - at) > _LIVE_KNOWN_TTL:
        return {}
    return _LIVE_KNOWN.get(prov) or {}


def remember_live_models(provider: str, rows: list[dict[str, Any]]) -> None:
    """Seed the registry (used by the catalog route's cache and by tests)."""
    prov = (provider or "").strip().lower()
    if not prov or not rows:
        return
    _LIVE_KNOWN[prov] = {str(r.get("id")): dict(r) for r in rows if r.get("id")}
    _LIVE_KNOWN_AT[prov] = time.time()


def forget_live_models(provider: str | None = None) -> None:
    if provider is None:
        _LIVE_KNOWN.clear()
        _LIVE_KNOWN_AT.clear()
        return
    _LIVE_KNOWN.pop(provider, None)
    _LIVE_KNOWN_AT.pop(provider, None)


_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def choose_default(
    models: list[dict[str, Any]],
    *,
    preferred: list[str | None] | None = None,
    loaded: list[str] | None = None,
) -> str | None:
    """Pick a sane default from a discovered/merged list.

    Order: a preferred id that is still listed → a loaded chat model → the
    first chat-capable model → the first model at all.
    """
    if not models:
        return None
    ids = [str(m.get("id")) for m in models if m.get("id")]
    idset = set(ids)
    for p in preferred or []:
        if p and p in idset:
            return p
    chat_ids = [
        str(m["id"]) for m in models if m.get("id") and m.get("chat", looks_like_chat_model(str(m["id"])))
    ]
    for loaded_id in loaded or []:
        if loaded_id in chat_ids:
            return loaded_id
    for m in models:
        if m.get("loaded") and str(m.get("id")) in chat_ids:
            return str(m["id"])
    if chat_ids:
        return chat_ids[0]
    return ids[0] if ids else None


def invalidate_ollama_detect_cache() -> None:
    """Drop the short-lived Ollama probe cache (tests / explicit re-detect)."""
    with _ollama_detect_lock:
        _ollama_detect_cache["key"] = None
        _ollama_detect_cache["ts"] = 0.0
        _ollama_detect_cache["value"] = None
    with _listen_lock:
        _listen_cache.clear()
        _listen_inflight.clear()


def detect_ollama_sync(
    base_url: str | None = None,
    timeout: float = 1.5,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Synchronous Ollama detection (tags only) for CLI / bootstrap paths.

    Resolution: explicit arg → ``OLLAMA_HOST`` → catalog default.
    Local hosts get a 150ms TCP precheck so a down daemon cannot burn the
    caller's 1.5s urllib timeout (Windows firewall often drops the SYN).
    Results are cached for a few seconds so desktop chrome polls stay cheap.
    """
    import json
    import urllib.error
    import urllib.request

    from remedy.interfaces.provider_catalog import PROVIDER_CATALOG

    url = (base_url or ollama_base_url_from_env() or PROVIDER_CATALOG["ollama"]["base_url"] or "").rstrip("/")
    tags_url = _root(url) + "/api/tags"
    now = time.monotonic()
    if not force:
        with _ollama_detect_lock:
            cached = _ollama_detect_cache["value"]
            if (
                _ollama_detect_cache["key"] == url
                and isinstance(cached, dict)
                and (now - float(_ollama_detect_cache["ts"] or 0)) < _OLLAMA_DETECT_TTL_S
            ):
                return dict(cached)

    def _store(result: dict[str, Any]) -> dict[str, Any]:
        with _ollama_detect_lock:
            _ollama_detect_cache["key"] = url
            _ollama_detect_cache["ts"] = time.monotonic()
            _ollama_detect_cache["value"] = dict(result)
        return dict(result)

    if _PRECHECK_LOCAL_LISTEN and url and _is_local_url(url) and not _local_host_listening_sync(url):
        return _store(
            {"available": False, "base_url": url, "models": [], "tags_url": tags_url}
        )

    models: list[str] = []
    try:
        req = urllib.request.Request(
            tags_url,
            headers={"Accept": "application/json", "User-Agent": "Remedy/detect-ollama"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8") or "{}")
        for m in body.get("models") or []:
            name = m.get("name") or m.get("model") or ""
            if name:
                short = name.removesuffix(":latest")
                if short not in models:
                    models.append(short)
        return _store({"available": True, "base_url": url, "models": models, "tags_url": tags_url})
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return _store({"available": False, "base_url": url, "models": [], "tags_url": tags_url})
