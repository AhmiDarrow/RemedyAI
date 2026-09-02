"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

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

    # -- updates ------------------------------------------------------------
    @app.get("/api/updates/check")
    async def check_updates(current: str | None = Query(default=None)):
        """Report package + desktop release versions.

        Desktop UI prefers the Tauri ``check_desktop_update`` command; this
        endpoint is the browser/dev fallback and a secondary path when Rust
        GitHub fetch fails.

        Optional ``current``: shell/app version to compare against (desktop
        package version). When omitted, uses the Python package version — which
        can lag or lead the installed EXE if the sidecar was rebuilt separately.
        """
        from remedy.interfaces.updater import _parse_version

        python_version = _remedy_version
        # Prefer explicit shell version so a newer sidecar cannot mask an
        # outdated desktop EXE (or vice versa).
        current_raw = (current or "").strip() or python_version
        current_norm = str(current_raw).lstrip("vV").strip() or python_version
        # Chrome polls this; PyPI + GitHub are ~500ms. Cache per current
        # version on this app so a restart still fetches once.
        now = time.monotonic()
        cache = getattr(app.state, "_updates_check_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            app.state._updates_check_cache = cache
        hit = cache.get(current_norm)
        if (
            isinstance(hit, tuple)
            and len(hit) == 2
            and (now - float(hit[0])) < 300.0
            and isinstance(hit[1], dict)
        ):
            return dict(hit[1])
        latest_python = None
        latest_desktop = None
        release_url = None
        installer_url = None
        errors: list[str] = []

        # Every fetch below runs in a worker thread. urlopen is blocking, and
        # this is an async route: done inline, one unreachable host froze the
        # whole local API — chat, streaming, everything — for up to 40 seconds
        # across the three calls.
        def _fetch(url: str, timeout: float) -> dict:
            import json as _json
            import urllib.request as _urllib

            req = _urllib.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "Remedy-Updater"},
            )
            # `_urllib` is already urllib.request (not the top-level package).
            with _urllib.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return _json.loads(resp.read().decode())

        try:
            data = await asyncio.to_thread(
                _fetch, "https://pypi.org/pypi/remedy-ai/json", 10
            )
            latest_python = data["info"]["version"]
        except Exception as e:
            errors.append(f"PyPI: {e}")

        # Prefer latest.json, then GitHub Releases API.
        for url in (
            "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json",
            "https://api.github.com/repos/AhmiDarrow/RemedyAI/releases/latest",
        ):
            try:
                data = await asyncio.to_thread(_fetch, url, 15)
                if "version" in data:
                    latest_desktop = str(data.get("version") or "").lstrip("vV")
                    release_url = (
                        "https://github.com/AhmiDarrow/RemedyAI/releases/latest"
                    )
                    installer_url = (
                        (data.get("platforms") or {})
                        .get("windows-x86_64", {})
                        .get("url")
                    ) or data.get("url")
                    break
                if "tag_name" in data:
                    latest_desktop = str(data.get("tag_name") or "").lstrip("vV")
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
        # Desktop installer is the product of record for the app.
        if latest_desktop and _parse_version(latest_desktop) > _parse_version(
            current_norm
        ):
            update_available = True
        elif (
            latest_python
            and not latest_desktop
            and _parse_version(latest_python) > _parse_version(current_norm)
        ):
            update_available = True

        # Require an installer URL before claiming a desktop update is installable.
        if (
            update_available
            and latest_desktop
            and not (installer_url and str(installer_url).strip())
        ):
            errors.append(
                "Newer desktop release found but no Windows installer URL on the release."
            )
            # Still flag available so the UI can open the releases page.
            # Install button needs installer_url; UpdateScreen checks it.

        result = {
            "current_version": current_norm,
            "python_version": python_version,
            "latest_python": latest_python,
            "latest_desktop": latest_desktop,
            "release_url": release_url,
            "installer_url": installer_url,
            "update_available": update_available,
            "error": " · ".join(errors) if errors else None,
        }
        cache[current_norm] = (now, dict(result))
        return result

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


