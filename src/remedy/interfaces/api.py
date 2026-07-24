"""REST API server -- FastAPI-based interface for Remedy.

Exposes chat sessions, streaming messages, memory, skills, commands,
models, agents, and webhook endpoints for the desktop and web UI.

Models: api_models.py  |  Helpers: api_support.py  |  Routes: create_app() below.
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
import time
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

    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework — Desktop & Web API",
    )

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
        "/docs",
        "/redoc",
        "/openapi.json",
        "/dashboard",
        "/api/status",
        "/api/auth/local-bootstrap",
        "/api/openapi.json",
        "/api/openapi.yaml",
    }
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
            if path in _AUTH_PUBLIC or path.startswith("/docs") or path.startswith("/redoc"):
                return await call_next(request)
            # SPA / static Web UI (GET only) — browser loads shell then bootstraps token
            if request.method in ("GET", "HEAD") and not path.startswith("/api"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            expected = f"Bearer {api_key}"
            # Constant-time compare for Bearer
            bearer_ok = hmac.compare_digest(auth.encode("utf-8"), expected.encode("utf-8"))
            alt = request.headers.get("X-Remedy-Token", "")
            alt_ok = bool(alt) and hmac.compare_digest(
                alt.encode("utf-8"), api_key.encode("utf-8")
            )
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
            if str(os.environ.get("REMEDY_HTTP_BOOTSTRAP", "1")).strip().lower() in (
                "0",
                "false",
                "no",
                "off",
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "http_bootstrap_disabled",
                        "detail": "Set REMEDY_HTTP_BOOTSTRAP=1 or use desktop IPC token.",
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
        # Health polls are high-frequency — don't spam the desktop console.
        path = request.url.path
        if path in ("/api/status",) or path.endswith("/api/status"):
            logger.debug("%s %s -> %d (%.0fms)", request.method, path, response.status_code, duration)
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
    """Locate built desktop SPA assets for browser WebUI mode."""
    env = (os.environ.get("REMEDY_WEBUI_DIR") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser())
    # Repo layout: src/remedy/interfaces/api.py → parents[3] = repo root
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "desktop" / "dist")
        candidates.append(parent / "ui")
        candidates.append(parent / "webui")
    # Next to frozen sidecar (packaged next to remedy-desktop.exe)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "ui",
                exe_dir / "webui",
                exe_dir / "desktop" / "dist",
                # Tauri resource dir (sibling of externalBin on some layouts)
                exe_dir / "resources" / "webui",
                exe_dir.parent / "webui",
                exe_dir.parent / "resources" / "webui",
            ]
        )
    # Meipass / _MEIPASS bundle (PyInstaller onefile extract)
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

    def _spa_file(full_path: str = ""):
        if full_path in ("", ".", "/"):
            return FileResponse(index)
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
            return FileResponse(index)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def webui_index():
        return FileResponse(index)

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
