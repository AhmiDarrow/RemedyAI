"""REST API server -- FastAPI-based interface for Remedy.

Exposes chat sessions, streaming messages, memory, skills, commands,
models, agents, and webhook endpoints for the desktop and web UI.

Models: api_models.py  |  Helpers: api_support.py  |  Routes: create_app() below.
"""

from __future__ import annotations

import atexit
import hmac
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

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
    # Let slash commands list skills without threading runtime everywhere.
    handle_slash_command._skills_registry = (  # type: ignore[attr-defined]
        getattr(runtime, "skills", None) if runtime is not None else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Register once: covers hard kill of uvicorn after lifespan teardown too.
        global _vision_atexit_registered
        if not _vision_atexit_registered:
            atexit.register(_shutdown_vision_decoder)
            _vision_atexit_registered = True

        # Start gateway messengers on uvicorn's event loop (not the pre-uvicorn
        # asyncio.run used during serve bootstrap — that loop is already closed).
        if gateway is not None and not getattr(gateway, "running", False):
            try:
                await gateway.start()
                logger.info(
                    "Gateway messengers started on API lifespan (channels=%s)",
                    [c.value for c in getattr(gateway, "channels", [])],
                )
            except Exception:
                logger.exception("Gateway start on lifespan failed")

        # Local model starts with Remedy when installed + enabled (vision + nano).
        try:
            import asyncio

            from remedy.vision.service import maybe_autostart_local_model

            cfg0 = load_config()

            def _bg_autostart() -> None:
                # RMB first when enabled: exclusive GPU host unloads Smol before load.
                rmb_ok = False
                try:
                    from remedy.runtime.rmb.config import load_rmb_json, merge_state
                    from remedy.runtime.rmb.service import start_rmb_server

                    home0 = (
                        cfg0.get("home_dir") if isinstance(cfg0, dict) else None
                    )
                    st = merge_state(load_rmb_json(home0))
                    rmb_wanted = bool(st.get("enabled") and st.get("auto_start", True))
                    if rmb_wanted:
                        rr = start_rmb_server(home_dir=home0, wait_s=120.0)
                        rmb_ok = bool(rr.get("ok"))
                        if rmb_ok:
                            logger.info(
                                "RMB local agent host auto-started "
                                "(SmolVLM suspended)"
                            )
                        else:
                            logger.info(
                                "RMB auto-start: %s", rr.get("error") or rr
                            )
                except Exception:
                    logger.exception("RMB auto-start background task failed")
                # Vision only if RMB did not take the host (failed start may clear skip)
                try:
                    from remedy.runtime.rmb.mode import should_skip_vision_stack

                    if rmb_ok or should_skip_vision_stack(
                        cfg0 if isinstance(cfg0, dict) else None
                    ):
                        logger.info(
                            "Skipping SmolVLM autostart — RMB exclusive host"
                        )
                        return
                except Exception:
                    if rmb_ok:
                        return
                try:
                    r = maybe_autostart_local_model(cfg0)
                    if r.get("ok"):
                        logger.info("Local model auto-started with Remedy")
                    elif not r.get("skipped"):
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

        # Self-inject idle scheduler: when enabled + idle, auto-run improvement rounds.
        # This is the auto-trigger; the on-command path (self_inject_round tool / skill)
        # always works regardless. Runs independently of any single turn.
        _self_inject_task: Any = None
        try:
            from remedy.core.self_inject import should_run_now

            _self_inject_enabled = bool(should_run_now()) if runtime is not None else False

            async def _self_inject_idle_loop() -> None:
                while True:
                    try:
                        await asyncio.sleep(60)
                        if runtime is None:
                            continue
                        if not should_run_now():
                            continue
                        # Run one round on the python tree (live-source sidecar).
                        try:
                            from remedy.core.self_inject import (
                                SelfInjectRound,
                                apply_or_rollback,
                                git_capture,
                                run_gate,
                            )

                            repo = _self_inject_repo()
                            round_ = SelfInjectRound(tree="python")
                            snap = await git_capture(repo)
                            round_ = await run_gate(round_, repo)
                            if round_.status == "green":
                                round_ = await apply_or_rollback(
                                    round_, repo, snap
                                )
                            else:
                                await apply_or_rollback(round_, repo, snap)
                            from remedy.core.self_inject import append_ledger

                            append_ledger(
                                round_, getattr(runtime, "home_dir", None)
                            )
                            logger.info(
                                "self-inject idle round %s status=%s outcome=%s",
                                round_.round_id,
                                round_.status,
                                round_.outcome,
                            )
                        except Exception:
                            logger.exception("self-inject idle round failed")
                    except Exception:
                        logger.debug("self-inject idle loop error", exc_info=True)

            def _self_inject_repo() -> Path:
                from pathlib import Path

                import remedy as _pkg

                cand = Path(_pkg.__file__).resolve().parent.parent.parent
                for _ in range(6):
                    if (cand / "pyproject.toml").is_file():
                        return cand
                    cand = cand.parent
                return Path.cwd()

            if _self_inject_enabled and runtime is not None:
                try:
                    _self_inject_task = asyncio.create_task(_self_inject_idle_loop())
                    logger.info("self-inject idle scheduler started")
                except Exception:
                    logger.debug("self-inject idle scheduler start skipped", exc_info=True)
        except Exception:
            logger.debug("self-inject scheduler setup skipped", exc_info=True)

        try:
            yield
        finally:
            if _self_inject_task is not None:
                try:
                    _self_inject_task.cancel()
                except Exception:
                    pass
            if gateway is not None and getattr(gateway, "running", False):
                try:
                    await gateway.stop()
                except Exception:
                    logger.debug("Gateway stop on shutdown failed", exc_info=True)
            logger.info("API shutdown: stopping vision decoder if running")
            _shutdown_vision_decoder()

    # Packaged desktop sidecar / opt-in: hide Swagger/ReDoc + default OpenAPI
    # JSON (S-AUTH-05). Dev/serve keep docs; set REMEDY_DISABLE_API_DOCS=0 to
    # force-enable even when frozen.
    _docs_env = str(os.environ.get("REMEDY_DISABLE_API_DOCS", "")).strip().lower()
    if _docs_env in ("0", "false", "no", "off"):
        _disable_api_docs = False
    elif _docs_env in ("1", "true", "yes", "on"):
        _disable_api_docs = True
    else:
        _disable_api_docs = bool(getattr(sys, "frozen", False))
    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework — Desktop & Web API",
        lifespan=lifespan,
        docs_url=None if _disable_api_docs else "/docs",
        redoc_url=None if _disable_api_docs else "/redoc",
        openapi_url=None if _disable_api_docs else "/openapi.json",
    )
    app.state.disable_api_docs = _disable_api_docs  # type: ignore[attr-defined]

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
    _AUTH_PUBLIC_PREFIXES = (
        "/api/webhooks/",
    )
    # Computer-use: host/jobs/ui require Bearer (same as the rest of the API).
    # Rust Desktop poller DPAPI-loads ``local_api_token`` and sends Authorization;
    # SPA ``hostFetch`` already attaches auth headers. Unauthenticated loopback
    # claim/complete was a same-user job-theft surface (S-AUTH-04).
    #
    # a11y push stays loopback-only without Bearer: legacy in-page inject uses
    # job_id (≥16 chars) as the capability secret, not the API token.
    _COMPUTER_A11Y_LOOPBACK_PREFIXES = (
        "/api/computer/a11y/",
    )

    def _client_is_loopback(request: Request) -> bool:
        host = ""
        if request.client is not None:
            host = (request.client.host or "").strip().lower()
        # Also trust X-Forwarded only when clearly local (dev proxies)
        if host in ("127.0.0.1", "::1", "localhost", "testclient"):
            return True
        return host.startswith("127.") or host == "::ffff:127.0.0.1"

    if api_key:
        app.state.api_key = api_key  # type: ignore[attr-defined]

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
            if not _disable_api_docs and (
                path.startswith("/docs") or path.startswith("/redoc")
            ):
                return await call_next(request)
            if any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
                return await call_next(request)
            # Legacy a11y inject: loopback + job_id secret only (no Bearer).
            if _client_is_loopback(request) and any(
                path.startswith(p) for p in _COMPUTER_A11Y_LOOPBACK_PREFIXES
            ):
                return await call_next(request)
            # SPA / static Web UI (GET only) — browser loads shell then bootstraps token
            if request.method in ("GET", "HEAD") and not path.startswith("/api"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            expected = f"Bearer {api_key}"
            # Constant-time compare — never raise on length mismatch (→ always 401).
            def _ct_eq(a: str, b: str) -> bool:
                ae, be = a.encode("utf-8"), b.encode("utf-8")
                if len(ae) != len(be):
                    # Still touch compare_digest shape for timing hygiene
                    hmac.compare_digest(ae, ae)
                    return False
                return hmac.compare_digest(ae, be)

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
            return await call_next(request)

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
        response = await call_next(request)
        duration = (time.time() - start) * 1000
        path = request.url.path
        method = request.method.upper()
        desktop = str(os.environ.get("REMEDY_DESKTOP_SIDECAR", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        # High-frequency polls at DEBUG so CLI `remedy serve` terminals stay readable
        # (Desktop computer-host + status bars used to flood INFO every few ms).
        quiet = method == "OPTIONS" or path in (
            "/api/status",
            "/api/ping",
            "/api/partner/status",
            "/api/checkpoints/latest",
            "/api/plans/latest",
            "/api/events/sessions",
            "/api/computer/jobs/next",
            "/api/computer/ui/command",
            "/api/computer/host/status",
            "/api/computer/host/hello",
        )
        if path.startswith("/api/computer/") and method in ("GET", "HEAD", "POST"):
            # hello / jobs complete are also high-frequency; keep failures loud.
            if response.status_code < 400 and method in ("GET", "HEAD"):
                quiet = True
            if path.endswith("/hello") and response.status_code < 400:
                quiet = True
        if desktop and method in ("GET", "HEAD") and response.status_code < 400:
            quiet = True
        if duration >= 500:
            logger.warning(
                "SLOW %s %s -> %d (%.0fms)",
                request.method,
                path,
                response.status_code,
                duration,
            )
        elif quiet:
            logger.debug(
                "%s %s -> %d (%.0fms)",
                request.method,
                path,
                response.status_code,
                duration,
            )
        else:
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

    # Optional browser Web UI: same React app as Desktop, served by the local API.
    # Prefer REMEDY_WEBUI_DIR, then repo desktop/dist (dev), then sidecar-adjacent ui/.
    _mount_web_ui(app)

    return app


def find_webui_dir() -> Path | None:
    """Locate built desktop SPA assets for browser WebUI mode.

    Prefer **live** ``desktop/dist`` (Vite build) over staged sidecar ``webui/``
    so ``npm run build`` updates WebUI without hunting stale target/debug copies.
    """
    env = (os.environ.get("REMEDY_WEBUI_DIR") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser())
    # Dev: monorepo checkout root (tauri:dev sets this)
    dev_root = (os.environ.get("REMEDY_DEV_ROOT") or "").strip()
    if dev_root:
        candidates.append(Path(dev_root).expanduser().resolve() / "desktop" / "dist")
    # Repo layout: src/remedy/interfaces/api.py → parents include repo root
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "desktop" / "dist")
    # Tauri debug externalBin lives at desktop/src-tauri/target/debug/*.exe
    # → desktop/dist is parents[3]/dist (prefer over staged webui/)
    if getattr(sys, "frozen", False):
        try:
            exe = Path(sys.executable).resolve()
            # .../desktop/src-tauri/target/debug/remedy-desktop.exe
            desktop_pkg = exe.parents[3]
            candidates.append(desktop_pkg / "dist")
        except (IndexError, OSError):
            pass
    # Staged copies next to frozen sidecar (packaged installs; after live dist)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "ui",
                exe_dir / "webui",
                exe_dir / "desktop" / "dist",
                exe_dir / "resources" / "webui",
                exe_dir.parent / "webui",
                exe_dir.parent / "resources" / "webui",
            ]
        )
    # Non-dist fallbacks (legacy layouts)
    for parent in here.parents:
        candidates.append(parent / "ui")
        candidates.append(parent / "webui")
    # Meipass / _MEIPASS bundle (PyInstaller onefile extract) — last resort
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        candidates.extend([mp / "webui", mp / "ui", mp / "desktop" / "dist"])
    seen: set[str] = set()
    for c in candidates:
        try:
            key = str(c.resolve())
        except OSError:
            key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_dir() and (c / "index.html").is_file():
            return c
    return None


_WEBUI_MISSING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Remedy WebUI</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0a0a1a; color: #e0e0e0;
           display: flex; min-height: 100vh; align-items: center; justify-content: center; margin: 0; }
    .card { max-width: 420px; padding: 1.75rem; border: 1px solid #1e1e3e; border-radius: 12px;
            background: #12122a; }
    h1 { color: #7c3aed; font-size: 1.35rem; margin: 0 0 0.75rem; }
    p { color: #aaa; font-size: 0.95rem; line-height: 1.45; margin: 0 0 0.75rem; }
    a { color: #a78bfa; }
    code { font-size: 0.85rem; color: #c4b5fd; }
  </style>
</head>
<body>
  <div class="card">
    <h1>WebUI assets not bundled</h1>
    <p>The local API is running, but the chat WebUI files were not found next to the server.</p>
    <p>Use the desktop app, or open the <a href="/dashboard">API dashboard</a>.</p>
    <p>Dev: build with <code>cd desktop &amp;&amp; npm run build</code>, then restart serve.
       Or set <code>REMEDY_WEBUI_DIR</code> to a folder that contains <code>index.html</code>.</p>
  </div>
</body>
</html>
"""


def _mount_web_ui(app: FastAPI) -> None:
    """Serve the chat SPA at / when a built UI directory exists."""
    from fastapi.responses import FileResponse, HTMLResponse
    from fastapi.staticfiles import StaticFiles

    web_dir = find_webui_dir()
    if web_dir is None:
        logger.info("WebUI assets not found — browser mode serves a helper page + /dashboard")

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        async def webui_missing():
            return HTMLResponse(_WEBUI_MISSING_HTML)

        app.state.webui_dir = None  # type: ignore[attr-defined]
        return

    assets = web_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="webui-assets")

    index = web_dir / "index.html"
    # HTML entry must revalidate so browsers pick up new hashed asset names after rebuild.
    _html_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }

    def _spa_file(full_path: str = ""):
        if full_path in ("", ".", "/"):
            return FileResponse(index, headers=_html_headers)
        if (
            full_path.startswith("api")
            or full_path.startswith("docs")
            or full_path.startswith("redoc")
            or full_path.startswith("dashboard")
            or full_path.startswith("openapi")
            or full_path.startswith("assets")
        ):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        # Prefer real static file (favicon, logo, etc.)
        candidate = (web_dir / full_path).resolve()
        try:
            candidate.relative_to(web_dir.resolve())
        except ValueError:
            return FileResponse(index, headers=_html_headers)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers=_html_headers)

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def webui_index():
        return FileResponse(index, headers=_html_headers)

    # SPA deep-link fallback (exclude /api, /docs, /dashboard)
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def webui_spa(full_path: str):
        return _spa_file(full_path)

    # Stash for CLI banner
    app.state.webui_dir = str(web_dir)  # type: ignore[attr-defined]
    logger.info("WebUI mounted from %s (open http://127.0.0.1:7400/)", web_dir)


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
            <h2>API Endpoints</h2>
            <p class="section-header">Chat & Sessions</p>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/chat</span> — legacy sync chat</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/chat/stream</span> (SSE) — legacy stream</div>
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
            <p class="section-header">Memory & Skills</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/memory/search?query=...</span> — search memory</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/memory/add</span> — add memory entry</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/skills</span> — list skills</div>
            <p class="section-header">Other</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/status</span> — system status</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/session-summaries</span> — legacy summaries</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/handoffs</span> — handoff notes</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/webhook/{source}</span> — receive webhook</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/openapi.yaml</span> — OpenAPI YAML</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/openapi.json</span> — OpenAPI JSON</div>
        </div>
    </div>
</body>
</html>"""
