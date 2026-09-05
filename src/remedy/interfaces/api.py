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

# High-frequency host polls (Go owns production). Failures stay loud; names
# remain so TestClient request-log helpers match historical quiet paths.
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
    Lifespan is TestClient teardown only — no production host autostart.
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

        try:
            yield
        finally:
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
    # Public allowlist is intentionally small (docs + token bootstrap).
    # Go owns production /api/ping|/api/status|/api/turn-active and the SPA.
    _AUTH_PUBLIC = {
        "/api/auth/local-bootstrap",
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
            # Public docs / bootstrap
            if path in _AUTH_PUBLIC:
                return await call_next(request)
            if not _disable_api_docs and (path.startswith("/docs") or path.startswith("/redoc")):
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
        # High-frequency polls at DEBUG (Go owns most of these in production).
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
    # WebUI SPA is Go-owned (httpapi/webui.go); no TestClient /dashboard twin.
    return app


def yaml_schema(app: FastAPI) -> str:
    """Convert OpenAPI JSON to YAML."""
    data = app.openapi()
    import io

    out = io.StringIO()
    yaml.dump(data, out, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return out.getvalue()
