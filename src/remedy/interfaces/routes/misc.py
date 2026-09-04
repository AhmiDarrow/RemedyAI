"""TestClient-only: misc app-control, project scan, and schema routes.

Go ``remedy-runtime`` owns production ``:7400``; this registrar is for pytest.
``/api/updates/check`` is Go-owned (not registered here).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import yaml
from fastapi import FastAPI, HTTPException, Query, Response

from remedy import __version__ as _remedy_version

logger = logging.getLogger(__name__)


def register_misc_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- app control (Remedy driving her own interface) --------------------
    @app.get("/api/app/command")
    async def app_command(take: bool = False):
        """Client polls this fast; Remedy enqueues UI actions via app_control.

        take=1 atomically removes the command so it dispatches exactly once.
        """
        from remedy.core.app_control import app_control_bus

        bus = app_control_bus()
        cmd = bus.take() if take else bus.peek()
        return {"command": cmd}

    # /api/updates/check lives on Go httpapi only.

    def _yaml_schema() -> str:
        import io

        data = app.openapi()
        buf = io.StringIO()
        yaml.safe_dump(data, buf, sort_keys=False)
        return buf.getvalue()

    # -- OpenAPI schema export -----------------------------------------------
    # Hidden when packaged/frozen or REMEDY_DISABLE_API_DOCS=1 (S-AUTH-05).
    if not getattr(getattr(app, "state", None), "disable_api_docs", False):

        @app.get("/api/openapi.yaml", include_in_schema=False)
        async def export_openapi_yaml():
            return Response(
                content=_yaml_schema(),
                media_type="application/yaml",
            )

        @app.get("/api/openapi.json", include_in_schema=False)
        async def export_openapi_json():
            return Response(
                content=json.dumps(app.openapi(), indent=2),
                media_type="application/json",
            )

    # -- project init scanner -------------------------------------------------
    @app.post("/api/projects/scan")
    async def scan_project(path: str = Query(default=".")):
        """Scan a project tree for language mix / deps (path-jailed).

        Never walk ``~/.remedy/auth`` or other protected secret trees, and never
        accept arbitrary absolute paths outside access-scope roots (was an
        unauthenticated recon vector when API auth is off / token is held).
        """
        from remedy.core.security import (
            is_protected_secret_path,
            refuse_protected_secret_path,
        )
        from remedy.core.workspace import (
            allowed_roots_for_scope,
            default_project_from_config,
            resolve_under_roots,
        )
        from remedy.interfaces.api_support import load_config

        raw = (path or ".").strip() or "."
        cfg = load_config() or {}
        scope = str(cfg.get("access_scope") or "home")
        project = default_project_from_config(cfg)
        if runtime is not None and hasattr(runtime, "effective_project_path"):
            with contextlib.suppress(Exception):
                project = runtime.effective_project_path()
        roots = allowed_roots_for_scope(scope, project)
        try:
            target = resolve_under_roots(raw, roots, access_scope=scope)
        except Exception as exc:
            raise HTTPException(400, f"Path not allowed: {exc}") from exc
        try:
            refuse_protected_secret_path(target)
        except Exception as exc:
            raise HTTPException(
                400, "Path not allowed: protected Remedy secrets location"
            ) from exc
        if not target.exists():
            raise HTTPException(404, f"Path not found: {path}")
        if not target.is_dir():
            target = target.parent
            try:
                refuse_protected_secret_path(target)
            except Exception as exc:
                raise HTTPException(
                    400, "Path not allowed: protected Remedy secrets location"
                ) from exc
            # Parent after file→dir fallback must still be under roots.
            try:
                resolve_under_roots(str(target), roots, access_scope=scope)
            except Exception as exc:
                raise HTTPException(400, f"Path not allowed: {exc}") from exc

        # Double-check: never rglob under auth even if roots were misconfigured.
        if is_protected_secret_path(target):
            raise HTTPException(
                400, "Path not allowed: protected Remedy secrets location"
            )

        files: dict[str, list[str]] = {
            "python": [],
            "javascript": [],
            "typescript": [],
            "rust": [],
            "other": [],
        }
        exts_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".mjs": "javascript",
            ".rs": "rust",
            ".c": "other",
            ".cpp": "other",
            ".h": "other",
            ".json": "other",
            ".yaml": "other",
            ".yml": "other",
            ".toml": "other",
            ".md": "other",
            ".txt": "other",
            ".css": "other",
            ".html": "other",
        }
        ignored = {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            ".next",
            "target",
            "auth",
        }
        def _walk_tree() -> None:
            # rglob + is_file stat every entry: on a deep or network/OneDrive
            # tree this blocks for seconds. Run it off the event loop so the
            # walk never freezes other sessions.
            for f in target.rglob("*"):
                if not f.is_file():
                    continue
                if any(p in ignored for p in f.parts):
                    continue
                # Skip any path that resolves into protected secrets mid-walk
                # (symlink escape / nested .remedy/auth).
                if is_protected_secret_path(f):
                    continue
                ext = f.suffix.lower()
                cat = exts_map.get(ext, "other")
                try:
                    rel = str(f.relative_to(target))
                except ValueError:
                    continue
                if len(files[cat]) < 100:
                    files[cat].append(rel)

        await asyncio.to_thread(_walk_tree)

        summary = {
            "path": str(target),
            "file_counts": {k: len(v) for k, v in files.items()},
            "top_files": files,
            "python_deps": "",
            "js_deps": "",
        }

        # try reading pyproject.toml or package.json for deps
        pp = target / "pyproject.toml"
        if pp.exists() and not is_protected_secret_path(pp):
            summary["python_deps"] = pp.read_text(
                encoding="utf-8", errors="replace"
            )[:2000]
        pj = target / "package.json"
        if pj.exists() and not is_protected_secret_path(pj):
            summary["js_deps"] = pj.read_text(
                encoding="utf-8", errors="replace"
            )[:2000]

        return summary

    # -- dashboard (simple HTML) ---------------------------------------------
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        from remedy.interfaces.api import DASHBOARD_HTML

        html = DASHBOARD_HTML.replace("{{version}}", _remedy_version)
        return Response(content=html, media_type="text/html")


