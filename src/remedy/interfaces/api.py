"""FastAPI route surface for pytest / TestClient only (optional tauri:dev harness).

Production ``:7400`` is owned by Go ``remedy-runtime``. ``remedy serve`` and
packaged Desktop exec that binary — they do **not** import this module or bind
uvicorn. ``create_app`` stays for in-process tests; ``tauri:dev`` may launch
``remedy`` as a launcher only (which then hands off to ``remedy-runtime``).
Python workers use ``python -m remedy.runtime.rmdy_tool_worker``.

Models: api_models.py  |  Helpers: api_support.py  |  Routes: create_app() below.
"""

from __future__ import annotations

import atexit
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import yaml
from fastapi import (
    FastAPI,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from remedy import __version__ as _remedy_version
from remedy.interfaces.api_models import (
    ChatRequest,
    ChatResponse,
    StatusResponse,
    WebhookPayload,
)
from remedy.interfaces.api_support import (
    handle_slash_command,
    load_config,
    sse_headers,
)

logger = logging.getLogger(__name__)

# High-frequency host polls. A fat ReAct turn blocks the event loop; these
# waiting ≥500ms is contention, not the poller being slow. Failures stay loud.
# Pollers still registered on the TestClient surface (Go owns the rest).
_SLOW_EXEMPT_PATHS = frozenset(
    {
        "/api/status",
        "/api/ping",
        "/api/turn-active",
        "/api/self-improve",
    }
)
_SLOW_WARN_MS = 500.0


def should_warn_slow(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
) -> bool:
    """True when the request log should emit a SLOW warning."""
    _ = method
    if float(duration_ms) < _SLOW_WARN_MS:
        return False
    if int(status_code) >= 400:
        return True
    return str(path or "") not in _SLOW_EXEMPT_PATHS


# Hot polls already sit at DEBUG; writing every ~150ms jobs/next into debug.log
# still burns disk during long Grok turns. Keep failures + slow quiet polls.
_QUIET_SILENT_MS = 100.0


def request_log_level(
    *,
    quiet: bool,
    status_code: int,
    duration_ms: float,
    slow: bool,
) -> str | None:
    """Return logging level name, or None to stay silent."""
    if slow:
        return "warning"
    if quiet and int(status_code) < 400 and float(duration_ms) < _QUIET_SILENT_MS:
        return None
    if quiet:
        return "debug"
    return "info"



_CLIENT_GONE_NAMES = frozenset({"EndOfStream", "ClientDisconnect", "BrokenResourceError"})


def _is_client_gone(exc: BaseException) -> bool:
    """True when the HTTP client disconnected mid-request (not a server fault).

    Match only the exact anyio/Starlette disconnect types, and only at the head
    of the exception or one ``__cause__`` hop. A broad substring scan over the
    whole ``__cause__``/``__context__`` chain used to reclassify genuine server
    bugs (a ``TypeError`` raised while a ``ClientDisconnect`` sat in its context)
    as ``404 Request aborted`` — that is exactly how the phone terminal's
    ``TypeError`` hid for so long. Fail loud on real faults instead.
    """
    if type(exc).__name__ in _CLIENT_GONE_NAMES:
        return True
    # One hop only — never a full-chain substring sweep.
    nxt = exc.__cause__ or exc.__context__
    return nxt is not None and type(nxt).__name__ in _CLIENT_GONE_NAMES


# Re-export models for existing `from remedy.interfaces.api import ChatRequest` callers.
__all__ = [
    "create_app",
    "yaml_schema",
    "ChatRequest",
    "ChatResponse",
    "StatusResponse",
    "WebhookPayload",
    "handle_slash_command",
    "load_config",
    "sse_headers",
]

_vision_atexit_registered = False


def _shutdown_vision_decoder() -> None:
    """Stop local llama-server processes on process exit (vision + RMB).

    Never resume Smol during teardown (would orphan llama-server).
    """
    home = None
    with suppress(Exception):
        cfg = load_config()
        if isinstance(cfg, dict) and cfg.get("home_dir"):
            home = cfg.get("home_dir")
    with suppress(Exception):
        from remedy.runtime.rmb.service import stop_rmb_server

        # Stop RMB first, without restarting vision
        stop_rmb_server(home_dir=home, resume_vision=False)
    with suppress(Exception):
        from remedy.vision.runtime import shutdown_vision_for_exit

        shutdown_vision_for_exit(home_dir=home)


def create_app(
    runtime=None,
    gateway=None,
    memory=None,
    title: str = "Remedy AI",
    version: str = _remedy_version,
    *,
    api_key: str = "",
) -> FastAPI:
    """Build the in-process FastAPI app for pytest / TestClient.

    Not a production HTTP server. Go ``remedy-runtime`` owns ``:7400``.
    """
    # Let slash commands list skills without threading runtime everywhere.
    handle_slash_command._skills_registry = (  # type: ignore[attr-defined]
        getattr(runtime, "skills", None) if runtime is not None else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Register once: covers hard kill of the TestClient process too.
        global _vision_atexit_registered
        if not _vision_atexit_registered:
            atexit.register(_shutdown_vision_decoder)
            _vision_atexit_registered = True

        # Prime the optional native route once at startup. Compatibility mode
        # returns immediately; auto/native probe only signed, bundled paths and
        # fall back to Python when unavailable. Keep /api/ping probe-free.
        try:
            import asyncio as _asyncio_native

            from remedy.runtime.native_runtime import initialize_native_runtime

            _cfg_native = load_config()
            await _asyncio_native.wait_for(
                _asyncio_native.to_thread(
                    initialize_native_runtime,
                    _cfg_native if isinstance(_cfg_native, dict) else None,
                ),
                timeout=3.0,
            )
        except TimeoutError:
            logger.warning("Native runtime startup probe timed out; using compatibility")
        except Exception:
            logger.debug("Native runtime startup probe skipped", exc_info=True)

        # Start gateway messengers on the app event loop (TestClient / ASGI).
        if gateway is not None and not getattr(gateway, "running", False):
            try:
                await gateway.start()
                logger.info(
                    "Gateway messengers started on API lifespan (channels=%s)",
                    [c.value for c in getattr(gateway, "channels", [])],
                )
            except Exception:
                logger.exception("Gateway start on lifespan failed")

        # Retention pass (attachments / shots / undo / logs / optional sessions).
        # Off the loop: it scans the whole home on disk, so on a large home it
        # would delay readiness and block the rest of lifespan startup.
        try:
            import asyncio as _asyncio_ret

            from remedy.core.retention import run_retention_pass

            _cfg_ret = load_config()
            _mem = getattr(runtime, "memory", None) if runtime is not None else None
            await _asyncio_ret.to_thread(
                run_retention_pass,
                _cfg_ret if isinstance(_cfg_ret, dict) else None,
                store=_mem,
            )
        except Exception:
            logger.debug("retention pass skipped", exc_info=True)

        # Vigil heartbeat starts with Remedy — no user action, ever. Inert
        # until the partner grants her time in conversation (soul_vigil tool);
        # budgets make the tick harmless. Local-only; never calls a provider.
        _vigil_thread = None
        try:
            from remedy.core.feature_maturity import soul_field_enabled

            if soul_field_enabled():
                from remedy.memory.soul.vigil import start_vigil_thread

                _cfg_v = load_config()
                _home_v = (
                    _cfg_v.get("home_dir") if isinstance(_cfg_v, dict) else None
                )
                _vigil_thread = start_vigil_thread(_home_v)
                logger.info("Vigil heartbeat started (inert until granted)")
        except Exception:
            logger.debug("vigil heartbeat start skipped", exc_info=True)

        # Remedy's clock + reach: fires stored reminders/due dates into the
        # durable outbox and (when the owner has channels) their messengers.
        # Local-only and cheap (small JSON read), so it always runs.
        _notify_thread = None
        try:
            import asyncio as _asyncio

            from remedy.core.notify import start_delivery_thread

            _cfg_r = load_config()
            _home_r = _cfg_r.get("home_dir") if isinstance(_cfg_r, dict) else None
            try:
                _main_loop = _asyncio.get_running_loop()
            except RuntimeError:
                _main_loop = None

            def _messenger_send(text: str) -> None:
                """Bridge the sync clock thread onto the async gateway."""
                if gateway is None or _main_loop is None:
                    return
                # ``suppress`` is what this module imports; the bare
                # ``contextlib`` name was never bound, so this raised NameError
                # on every push — and notify.deliver_due wraps the call in its
                # own suppress, so every reminder was recorded as delivered
                # while no messenger ever heard about it.
                with suppress(Exception):
                    _asyncio.run_coroutine_threadsafe(
                        gateway.broadcast(text), _main_loop
                    )

            _notify_thread = start_delivery_thread(
                _home_r, messenger_send=_messenger_send
            )
            logger.info("Reminder clock + delivery started")
        except Exception:
            logger.debug("reminder clock start skipped", exc_info=True)

        # Local model starts with Remedy when installed + enabled (vision + nano).
        try:
            import asyncio

            cfg0 = load_config()

            def _bg_autostart() -> None:
                # RMB first when enabled: exclusive GPU host unloads Smol before load.
                rmb_ok = False
                try:
                    from remedy.runtime.rmb.config import load_rmb_json, merge_state
                    from remedy.runtime.rmb.service import ensure_rmb_server

                    home0 = cfg0.get("home_dir") if isinstance(cfg0, dict) else None
                    st = merge_state(load_rmb_json(home0))
                    from remedy.core.feature_maturity import rmb_enabled

                    rmb_wanted = bool(
                        rmb_enabled(cfg0 if isinstance(cfg0, dict) else None)
                        and st.get("enabled")
                        and st.get("auto_start", False)
                    )
                    if rmb_wanted:
                        from remedy.runtime.rmb.service import (
                            adopt_existing_host,
                            ensure_rmb_watchdog,
                        )

                        ensure_rmb_watchdog(home0)
                        with suppress(Exception):
                            adopt_existing_host(home0)
                        # Honor persisted user Stop after recycle — do not
                        # start_rmb_server (that used to wipe stay-off).
                        rr = ensure_rmb_server(home_dir=home0, wait_s=120.0)
                        rmb_ok = bool(rr.get("ok"))
                        if rmb_ok:
                            logger.info(
                                "RMB local agent host auto-started (SmolVLM suspended) watchdog=on"
                            )
                        else:
                            logger.info("RMB auto-start: %s", rr.get("error") or rr)
                except Exception:
                    logger.exception("RMB auto-start background task failed")
                # Voice models (Kokoro / whisper / smart-turn) always download
                # on first run — not a Settings option.
                try:
                    from remedy.voice.service import ensure_voice_assets

                    home0 = cfg0.get("home_dir") if isinstance(cfg0, dict) else None
                    vr = ensure_voice_assets(home0)
                    if vr.get("started"):
                        logger.info("Voice assets first-run download: %s", vr.get("started"))
                except Exception:
                    logger.exception("Voice assets first-run ensure failed")
                # Local OpenSERP (~10 MB) so web_search has a real backend.
                # Download is non-blocking; DuckDuckGo HTML covers the gap.
                try:
                    from remedy.runtime.web_search_host import schedule_ensure

                    home0 = cfg0.get("home_dir") if isinstance(cfg0, dict) else None
                    web_on = True
                    if isinstance(cfg0, dict) and "web_tools_enabled" in cfg0:
                        web_on = bool(cfg0.get("web_tools_enabled"))
                    schedule_ensure(home0, enabled=web_on)
                except Exception:
                    logger.exception("web search host first-run ensure failed")
                # Claimidx is an isolated, private-by-default first-run
                # dependency. Installation/start happen in their own daemon
                # thread so an offline package index never delays Remedy.
                try:
                    from remedy.runtime.claimidx_host import (
                        schedule_ensure as schedule_claimidx,
                    )

                    home0 = cfg0.get("home_dir") if isinstance(cfg0, dict) else None
                    schedule_claimidx(home0)
                except Exception:
                    logger.exception("Claimidx first-run ensure failed")
                # Her voice should be ready before she is asked to speak: load
                # the engines now (Kokoro; Chatterbox too when HQ is on) so
                # the first sentence is not a twenty-second wait.
                try:
                    from remedy.voice.service import start_voice_evolution, warm_voice_engines

                    start_voice_evolution(home0, memory)
                    warm_voice_engines(
                        home0,
                        gender=str(cfg0.get("agent_gender") or "female")
                        if isinstance(cfg0, dict)
                        else None,
                    )
                except Exception:
                    logger.exception("Voice warm-up failed")

                # Vision files always download on first run. llama-server start
                # is skipped when RMB already owns the GPU host.
                try:
                    from remedy.runtime.rmb.mode import should_skip_vision_stack
                    from remedy.vision.service import maybe_ensure_local_model

                    skip_smol = bool(
                        rmb_ok
                        or should_skip_vision_stack(
                            cfg0 if isinstance(cfg0, dict) else None
                        )
                    )
                    r = maybe_ensure_local_model(cfg0)
                    # skipped=True + ok=True is RMB owning the GPU host — not
                    # a started SmolVLM. Live 2026-08-27 logged "Local model
                    # auto-started" while 8787 was closed and vision was skipped.
                    if r.get("skipped"):
                        logger.info(
                            "Local vision autostart skipped (%s)",
                            r.get("reason") or "not started",
                        )
                    elif skip_smol and r.get("ok"):
                        logger.info(
                            "Local vision download underway; SmolVLM start skipped "
                            "(RMB exclusive host)"
                        )
                    elif r.get("ok"):
                        logger.info("Local model auto-started with Remedy")
                    else:
                        logger.info(
                            "Local model auto-start: %s",
                            r.get("error") or r.get("reason") or r,
                        )
                except Exception:
                    logger.exception("Local model auto-start background task failed")

            # Non-blocking: model load can take tens of seconds
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _bg_autostart)
        except Exception:
            logger.debug("Local model auto-start schedule skipped", exc_info=True)

        # Unattended self-improve: start whenever the feature is enabled, not
        # only if the process is already idle at boot. Organism ticks (skill
        # lifecycle, dream, persist) run every cycle; code self-heal waits for
        # the user-idle window inside run_unattended_improve.
        _self_inject_task: Any = None
        try:
            from remedy.core.self_inject import is_enabled as _si_enabled

            _force = os.environ.get("REMEDY_SELF_INJECT_FORCE") == "1"
            _self_inject_enabled = bool(runtime is not None and (_si_enabled() or _force))

            async def _self_inject_idle_loop() -> None:
                first = True
                while True:
                    try:
                        # First tick quickly after boot so learning starts without
                        # a user prompt; then settle to a 60s cadence.
                        await asyncio.sleep(5 if first else 60)
                        first = False
                        if runtime is None:
                            continue
                        if not _si_enabled() and os.environ.get("REMEDY_SELF_INJECT_FORCE") != "1":
                            continue
                        try:
                            from remedy.core.self_inject import run_unattended_improve

                            home = getattr(runtime, "home_dir", None) or getattr(
                                getattr(runtime, "config", None), "home_dir", None
                            )
                            result = await run_unattended_improve(runtime, home=home)
                            org = result.get("organism") or {}
                            code = result.get("code")
                            logger.info(
                                "self-improve tick skills=%s dreamed=%s code=%s idle_s=%s",
                                org.get("skills_refined", 0),
                                org.get("dreamed", False),
                                (code or {}).get("outcome")
                                or (code or {}).get("skipped")
                                or "none",
                                result.get("idle_s"),
                            )
                        except Exception:
                            logger.exception("self-improve tick failed")
                    except Exception:
                        logger.debug("self-inject idle loop error", exc_info=True)

            if _self_inject_enabled:
                try:
                    _self_inject_task = asyncio.create_task(_self_inject_idle_loop())
                    logger.info("self-improve idle scheduler started")
                except Exception:
                    logger.debug("self-inject idle scheduler start skipped", exc_info=True)
        except Exception:
            logger.debug("self-inject scheduler setup skipped", exc_info=True)

        # User-listed MCP servers: spawn on the live loop, once, in the
        # background so a slow npx install never delays the API coming up.
        _mcp_task: Any = None
        if runtime is not None and getattr(runtime, "_mcp_bridge", None) is not None:
            try:
                import asyncio as _asyncio_mcp

                from remedy.core.agent_mcp_bridge import ensure_connected

                _mcp_task = _asyncio_mcp.create_task(ensure_connected(runtime))
            except Exception:
                logger.debug("MCP bridge startup skipped", exc_info=True)

        # Standing hive posts: wake their pulse loops so a serve restart does
        # not drop the job. Foragers are one-shot and stay reported on disk.
        if runtime is not None:
            try:
                from remedy.core.hive.pulse import resume_posts

                n_posts = resume_posts(runtime)
                if n_posts:
                    logger.info("Hive standing posts resumed count=%s", n_posts)
            except Exception:
                logger.debug("hive post resume skipped", exc_info=True)

        try:
            yield
        finally:
            try:
                from remedy.core.hive.pulse import stop_all_posts

                stop_all_posts()
            except Exception:
                logger.debug("hive post stop skipped", exc_info=True)
            if _mcp_task is not None:
                with suppress(Exception):
                    _mcp_task.cancel()
            if runtime is not None and getattr(runtime, "_mcp_bridge", None) is not None:
                try:
                    from remedy.core.agent_mcp_bridge import shutdown_mcp_bridge

                    await shutdown_mcp_bridge(runtime)
                except Exception:
                    logger.debug("MCP bridge shutdown failed", exc_info=True)
            if _vigil_thread is not None:
                with suppress(Exception):
                    _vigil_thread._vigil_stop.set()  # type: ignore[attr-defined]
            if _self_inject_task is not None:
                with suppress(Exception):
                    _self_inject_task.cancel()
            if gateway is not None and getattr(gateway, "running", False):
                try:
                    await gateway.stop()
                except Exception:
                    logger.debug("Gateway stop on shutdown failed", exc_info=True)
            logger.info("API shutdown: stopping vision decoder if running")
            _shutdown_vision_decoder()
            with suppress(Exception):
                from remedy.runtime.web_search_host import stop as stop_web_search_host

                stop_web_search_host()
            with suppress(Exception):
                from remedy.runtime.claimidx_host import stop as stop_claimidx

                stop_claimidx()
            # The shared aiohttp session behind every LLM call: nothing else
            # ever closed it, so its connection pool outlived the server.
            try:
                from remedy.core.agent_llm import aclose_shared_session

                await aclose_shared_session()
            except Exception:
                logger.debug("shared LLM session close on shutdown failed", exc_info=True)

    # Frozen / opt-in: hide Swagger/ReDoc + default OpenAPI JSON (S-AUTH-05).
    # TestClient keeps docs unless REMEDY_DISABLE_API_DOCS=1; set
    # REMEDY_DISABLE_API_DOCS=0 to force-enable even when frozen.
    _docs_env = str(os.environ.get("REMEDY_DISABLE_API_DOCS", "")).strip().lower()
    if _docs_env in ("0", "false", "no", "off"):
        _disable_api_docs = False
    elif _docs_env in ("1", "true", "yes", "on"):
        _disable_api_docs = True
    else:
        from remedy.core.runtime_identity import is_frozen_install

        _disable_api_docs = is_frozen_install()
    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework — Desktop & Web API",
        lifespan=lifespan,
        docs_url=None if _disable_api_docs else "/docs",
        redoc_url=None if _disable_api_docs else "/redoc",
        openapi_url=None if _disable_api_docs else "/openapi.json",
    )
    app.state.disable_api_docs = _disable_api_docs

    # CORS: REMEDY_CORS_ORIGINS env wins, then config.toml `cors_origins`, else safe defaults.
    # NEVER allow "*" when API auth is enabled — any website could read loopback bootstrap.
    cors_origins_env = os.environ.get("REMEDY_CORS_ORIGINS", "").strip()
    if cors_origins_env == "*":
        cors_origins = ["*"]
    elif cors_origins_env:
        cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    else:
        try:
            _cfg = load_config()
        except Exception:
            _cfg = {}
        cfg_origins = _cfg.get("cors_origins") if isinstance(_cfg, dict) else None
        if cfg_origins == "*" or cfg_origins == ["*"]:
            cors_origins = ["*"]
        elif isinstance(cfg_origins, str) and cfg_origins.strip():
            cors_origins = [o.strip() for o in cfg_origins.split(",") if o.strip()]
        elif isinstance(cfg_origins, list) and cfg_origins:
            cors_origins = [str(o).strip() for o in cfg_origins if str(o).strip()]
        else:
            # Safe defaults for local desktop/dev (include Tauri 2 custom-protocol origins)
            cors_origins = [
                "http://localhost:1420",
                "http://127.0.0.1:1420",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://127.0.0.1:7400",
                "http://localhost:7400",
                "tauri://localhost",
                "http://tauri.localhost",
                "https://tauri.localhost",
                "http://asset.localhost",
                "https://asset.localhost",
                "http://ipc.localhost",
                "https://ipc.localhost",
            ]
    # Owner power: they may still set explicit origin lists. Star is blocked when
    # api_key is set (browser could otherwise steal the bootstrap token).
    if api_key and cors_origins == ["*"]:
        logger.error(
            "CORS '*' refused while API auth is enabled (would expose local-bootstrap). "
            "Using loopback defaults. Set REMEDY_CORS_ORIGINS to explicit origins if needed."
        )
        cors_origins = [
            "http://127.0.0.1:7400",
            "http://localhost:7400",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "http://asset.localhost",
            "https://asset.localhost",
            "http://ipc.localhost",
            "https://ipc.localhost",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=cors_origins != ["*"],
    )

    # Local agent API: auth is ON by default when a key is available.
    # Public allowlist is intentionally small (health + docs + token bootstrap).
    _AUTH_PUBLIC = {
        "/dashboard",
        "/api/status",
        "/api/ping",
        "/api/turn-active",
        "/api/auth/local-bootstrap",
        # Google OAuth browser redirect (state is one-time secret; no bearer).
        "/api/assistant/google/callback",
    }
    if not _disable_api_docs:
        _AUTH_PUBLIC.update(
            {
                "/docs",
                "/redoc",
                "/openapi.json",
                "/api/openapi.json",
                "/api/openapi.yaml",
            }
        )
    # Messenger platform webhooks cannot send our Bearer token; they authenticate
    # via their own verify tokens / HMAC / JWT inside the route handlers.
    # Generic CI-style ``/api/webhook/{source}`` is also public at the middleware
    # layer so ``X-Remedy-Webhook-Secret`` can reach the route; the handler itself
    # fails closed (Bearer **or** webhook secret required when auth is on).
    _AUTH_PUBLIC_PREFIXES = (
        "/api/webhooks/",
        "/api/webhook/",
    )

    if api_key:
        app.state.api_key = api_key

        @app.middleware("http")
        async def require_auth(request: Request, call_next):
            path = request.url.path
            # CORS preflight must not require Bearer. Browsers / Tauri webviews send
            # OPTIONS without Authorization; a 401 here becomes opaque "Failed to fetch"
            # (looks like the server is down) and breaks xAI OAuth + all JSON API calls.
            if request.method == "OPTIONS":
                return await call_next(request)
            # Public docs / health / bootstrap
            if path in _AUTH_PUBLIC:
                return await call_next(request)
            if not _disable_api_docs and (path.startswith("/docs") or path.startswith("/redoc")):
                return await call_next(request)
            if any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
                return await call_next(request)
            # SPA / static Web UI (GET only) — browser loads shell then bootstraps token
            if request.method in ("GET", "HEAD") and not path.startswith("/api"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            expected = f"Bearer {api_key}"

            # Constant-time compare — never raise on length mismatch (→ always 401).
            from remedy.core.security import secret_equals as _ct_eq

            bearer_ok = _ct_eq(auth, expected)
            alt = request.headers.get("X-Remedy-Token", "")
            alt_ok = bool(alt) and _ct_eq(alt, api_key)
            if not (bearer_ok or alt_ok):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Unauthorized",
                        "detail": "Missing or invalid Bearer token. "
                        "Desktop loads it automatically; CLI: REMEDY_API_KEY.",
                    },
                )
            try:
                return await call_next(request)
            except Exception as exc:
                if _is_client_gone(exc):
                    return JSONResponse(
                        status_code=404,
                        content={"detail": "Request aborted"},
                    )
                raise

        @app.get("/api/auth/local-bootstrap")
        async def local_bootstrap(request: Request):
            """Loopback-only: return the local API token for desktop/web clients.

            Not a remote auth endpoint — only 127.0.0.1 / ::1 may call this.
            Prefer Tauri ``get_local_api_token`` when available (no HTTP).
            Same-user processes on this machine can always reach loopback; that is
            the owner-power boundary (malware as your user already owns the box).
            """
            client = (request.client.host if request.client else "") or ""
            # Starlette TestClient uses host "testclient"
            if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
                return JSONResponse(status_code=403, content={"error": "loopback only"})
            # DNS-rebinding guard: a page at evil.com can rebind its DNS to
            # 127.0.0.1 (so request.client.host passes) but its Host header
            # still carries the attacker origin. A real loopback client sends
            # Host: 127.0.0.1[:port] / localhost[:port]. Reject anything else
            # so the token can't be exfiltrated to a rebound origin.
            host_hdr = (request.headers.get("host") or "").split(":")[0].strip().lower()
            if host_hdr and host_hdr not in ("127.0.0.1", "::1", "localhost", "testclient", "testserver", "[", ""):
                logger.warning("local-bootstrap refused: non-loopback Host %r", host_hdr)
                return JSONResponse(
                    status_code=403,
                    content={"error": "host not loopback (rebinding blocked)"},
                )
            # Optional owner opt-out of HTTP bootstrap (desktop-only token channel)
            from remedy.interfaces.local_auth import http_bootstrap_enabled

            if not http_bootstrap_enabled():
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "http_bootstrap_disabled",
                        "detail": (
                            "Browser token bootstrap is off. Desktop still uses IPC "
                            "(full power). Enable Settings → Allow browser token "
                            "bootstrap, or set REMEDY_HTTP_BOOTSTRAP=1 for Web UI."
                        ),
                    },
                )
            logger.info("local-bootstrap issued to %s", client)
            return {
                "token": api_key,
                "auth_required": True,
                "note": "loopback-only; same Windows user can call this",
            }

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.time()
        try:
            response = await call_next(request)
        except Exception as exc:
            # BaseHTTPMiddleware turns client-abort / session-delete races into
            # EndOfStream. That is not a 500 — the caller went away.
            if _is_client_gone(exc):
                return JSONResponse(
                    status_code=404,
                    content={"detail": "Request aborted"},
                )
            raise
        duration = (time.time() - start) * 1000
        path = request.url.path
        method = request.method.upper()
        from remedy.core.runtime_identity import is_desktop_sidecar

        desktop = is_desktop_sidecar()
        # High-frequency polls at DEBUG so CLI `remedy serve` terminals stay readable
        # (Desktop computer-host + status bars used to flood INFO every few ms).
        quiet = method == "OPTIONS" or path in (
            "/api/status",
            "/api/ping",
            "/api/turn-active",
            "/api/self-improve",
        )
        if desktop and method in ("GET", "HEAD") and response.status_code < 400:
            quiet = True
        slow = should_warn_slow(method, path, response.status_code, duration)
        level = request_log_level(
            quiet=quiet,
            status_code=response.status_code,
            duration_ms=duration,
            slow=slow,
        )
        if level == "warning":
            logger.warning(
                "SLOW %s %s -> %d (%.0fms)",
                request.method,
                path,
                response.status_code,
                duration,
            )
        elif level == "debug":
            logger.debug(
                "%s %s -> %d (%.0fms)",
                request.method,
                path,
                response.status_code,
                duration,
            )
        elif level == "info":
            logger.info(
                "%s %s -> %d (%.0fms)",
                request.method,
                path,
                response.status_code,
                duration,
            )
        return response

    from remedy.interfaces.routes import register_all_routes

    register_all_routes(app, runtime=runtime, gateway=gateway, memory=memory)
    # WebUI SPA is Go-owned (httpapi/webui.go); TestClient keeps /dashboard only.
    return app


def yaml_schema(app: FastAPI) -> str:
    """Convert OpenAPI JSON to YAML."""
    data = app.openapi()
    import io

    out = io.StringIO()
    yaml.dump(data, out, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return out.getvalue()


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remedy AI - Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: system-ui, sans-serif; background: #0a0a1a; color: #e0e0e0; padding: 2rem; }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { color: #7c3aed; font-size: 2rem; margin-bottom: 0.5rem; }
        .subtitle { color: #888; margin-bottom: 2rem; }
        .card { background: #12122a; border: 1px solid #1e1e3e; border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; }
        .card h2 { color: #a78bfa; margin-bottom: 1rem; font-size: 1.1rem; }
        .stat { display: flex; justify-content: space-between; padding: 0.3rem 0; border-bottom: 1px solid #1e1e3e; }
        .stat:last-child { border-bottom: none; }
        .stat-label { color: #888; }
        .stat-value { color: #e0e0e0; font-weight: 600; }
        .endpoint { font-family: monospace; background: #0a0a1a; padding: 0.5rem 1rem; border-radius: 4px; margin: 0.3rem 0; }
        .method { color: #7c3aed; font-weight: bold; margin-right: 0.5rem; }
        .path { color: #e0e0e0; }
        .ok { color: #22c55e; }
        .err { color: #ef4444; }
        .section-header { color: #6366f1; font-size: 0.9rem; margin: 1rem 0 0.5rem 0; text-transform: uppercase; letter-spacing: 0.05em; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Remedy AI</h1>
        <p class="subtitle">Self-improving, multi-channel AI agent framework v{{version}}</p>

        <div class="card">
            <h2>Status</h2>
            <div class="stat"><span class="stat-label">Version</span><span class="stat-value">{{version}}</span></div>
            <div class="stat"><span class="stat-label">API</span><span class="stat-value ok">Online</span></div>
        </div>

        <div class="card">
            <h2>API Endpoints (TestClient surface)</h2>
            <p class="section-header">Chat & Sessions</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/sessions</span> — list chat sessions</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions</span> — create chat session</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/sessions/{id}</span> — get session</div>
            <div class="endpoint"><span class="method">PATCH</span><span class="path">/api/sessions/{id}</span> — rename session</div>
            <div class="endpoint"><span class="method">DELETE</span><span class="path">/api/sessions/{id}</span> — delete session</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/abort</span> — stop generation</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/sessions/{id}/messages</span> — list messages</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/messages</span> — sync send</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/messages/stream</span> (SSE) — structured events</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/command</span> — slash command</div>
            <p class="section-header">Models & Agents</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/models</span> — list LLM models</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/agents</span> — list agent profiles</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/commands</span> — slash commands</div>
            <p class="section-header">Other</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/status</span> — system status</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/self-improve</span> — unattended self-improve clock + last tick</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/diagnostics</span> — health diagnostics</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/openapi.yaml</span> — OpenAPI YAML</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/openapi.json</span> — OpenAPI JSON</div>
        </div>
    </div>
</body>
</html>"""
