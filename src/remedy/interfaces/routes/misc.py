"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from remedy import __version__ as _remedy_version
from remedy.core.errors import SecurityError
from remedy.core.security import safe_path
from remedy.interfaces.api_models import (
    AttachmentRef,
    AttachmentUploadRequest,
    ChatRequest,
    ChatResponse,
    CommandRequest,
    CreateSessionRequest,
    MemoryAddRequest,
    MemorySearchRequest,
    SendMessageRequest,
    SettingsUpdateRequest,
    SkillInfo,
    StatusResponse,
    UpdateSessionRequest,
    WebhookPayload,
)
from remedy.interfaces.api_support import (
    _apply_llm_to_runtime,
    _BUILTIN_AGENTS,
    _BUILTIN_COMMANDS,
    _BUILTIN_MODELS,
    _default_config_path,
    _find_config_path,
    _load_config_cached,
    _serialize_toml,
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    _write_config,
    handle_slash_command,
    load_config,
    sse_headers,
)
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    catalog_models_for_provider,
    needs_first_run_setup,
    normalize_llm_settings,
    provider_credentials_ready,
)
from remedy.interfaces.config import _is_local_url
from remedy.models import (
    ChannelKind,
    ChatMessageRole,
    EventKind,
    GatewayEvent,
    MemoryEntryType,
)

logger = logging.getLogger(__name__)


def register_misc_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- updates ------------------------------------------------------------
    @app.get("/api/updates/check")
    async def check_updates():
        """Report package + desktop release versions.

        Desktop UI prefers the Tauri ``check_desktop_update`` command; this
        endpoint is the browser/dev fallback and a secondary path when Rust
        GitHub fetch fails.
        """
        current = _remedy_version
        latest_python = None
        latest_desktop = None
        release_url = None
        installer_url = None
        errors: list[str] = []

        try:
            import json as _json
            import urllib.request as _urllib

            req = _urllib.Request(
                "https://pypi.org/pypi/remedy-ai/json",
                headers={"Accept": "application/json", "User-Agent": "Remedy-Updater"},
            )
            # `_urllib` is already urllib.request (not the top-level package).
            with _urllib.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
                latest_python = data["info"]["version"]
        except Exception as e:
            errors.append(f"PyPI: {e}")

        # Prefer latest.json, then GitHub Releases API.
        for url in (
            "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json",
            "https://api.github.com/repos/AhmiDarrow/RemedyAI/releases/latest",
        ):
            try:
                import json as _json
                import urllib.request as _urllib

                req = _urllib.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Remedy-Updater",
                    },
                )
                with _urllib.urlopen(req, timeout=10) as resp:
                    data = _json.loads(resp.read().decode())
                if "version" in data:
                    latest_desktop = str(data.get("version") or "").lstrip("v")
                    release_url = data.get("url") or (
                        "https://github.com/AhmiDarrow/RemedyAI/releases/latest"
                    )
                    installer_url = (
                        (data.get("platforms") or {})
                        .get("windows-x86_64", {})
                        .get("url")
                    ) or data.get("url")
                    break
                if "tag_name" in data:
                    latest_desktop = str(data.get("tag_name") or "").lstrip("v")
                    release_url = data.get("html_url") or (
                        "https://github.com/AhmiDarrow/RemedyAI/releases/latest"
                    )
                    for asset in data.get("assets") or []:
                        name = str(asset.get("name") or "")
                        lower = name.lower()
                        if name.endswith(("-setup.exe", "_x64-setup.exe")) or (
                            name.endswith(".exe")
                            and ("setup" in lower or "remedy" in lower)
                        ):
                            installer_url = asset.get("browser_download_url")
                            break
                    break
            except Exception as e:
                errors.append(f"GitHub ({url.split('/')[-1]}): {e}")

        update_available = False
        from remedy.interfaces.updater import _parse_version

        # Desktop installer is the product of record for the app.
        if latest_desktop and _parse_version(latest_desktop) > _parse_version(current):
            update_available = True
        elif (
            latest_python
            and not latest_desktop
            and _parse_version(latest_python) > _parse_version(current)
        ):
            update_available = True

        return {
            "current_version": current,
            "latest_python": latest_python,
            "latest_desktop": latest_desktop,
            "release_url": release_url,
            "installer_url": installer_url,
            "update_available": update_available,
            "error": " · ".join(errors) if errors else None,
        }

    def _yaml_schema() -> str:
        import io

        data = app.openapi()
        buf = io.StringIO()
        yaml.safe_dump(data, buf, sort_keys=False)
        return buf.getvalue()

    # -- OpenAPI schema export -----------------------------------------------
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
        target = Path(path).resolve()
        if not target.exists():
            raise HTTPException(404, f"Path not found: {path}")
        if not target.is_dir():
            target = target.parent

        files: dict[str, list[str]] = {"python": [], "javascript": [], "typescript": [], "rust": [], "other": []}
        exts_map = {
            ".py": "python", ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript",
            ".rs": "rust", ".c": "other", ".cpp": "other", ".h": "other",
            ".json": "other", ".yaml": "other", ".yml": "other", ".toml": "other",
            ".md": "other", ".txt": "other", ".css": "other", ".html": "other",
        }
        ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".next", "target"}
        for f in target.rglob("*"):
            if f.is_file() and not any(p in ignored for p in f.parts):
                ext = f.suffix.lower()
                cat = exts_map.get(ext, "other")
                rel = str(f.relative_to(target))
                files[cat].append(rel)
                if len(files[cat]) >= 100:
                    continue

        summary = {
            "path": str(target),
            "file_counts": {k: len(v) for k, v in files.items()},
            "top_files": files,
            "python_deps": "",
            "js_deps": "",
        }

        # try reading pyproject.toml or package.json for deps
        pp = target / "pyproject.toml"
        if pp.exists():
            summary["python_deps"] = pp.read_text(encoding="utf-8", errors="replace")[:2000]
        pj = target / "package.json"
        if pj.exists():
            summary["js_deps"] = pj.read_text(encoding="utf-8", errors="replace")[:2000]

        return summary

    # -- dashboard (simple HTML) ---------------------------------------------
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        from remedy.interfaces.api import DASHBOARD_HTML

        html = DASHBOARD_HTML.replace("{{version}}", _remedy_version)
        return Response(content=html, media_type="text/html")


