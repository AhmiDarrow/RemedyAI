"""Built-in ComfyUI tool helpers for Remedy (local image generation).

Discovery is **portable** — works on any machine without hard-coded user paths:

1. Explicit config / env (``comfyui_url``, ``comfyui_home``, ``COMFYUI_*``)
2. Live HTTP probes on common loopback ports
3. Running-process command lines (when available)
4. Bounded shallow search under the user home + a few roots for folders
   named ``ComfyUI`` / ``comfyui`` that contain ``main.py``

Agents must never full-disk scan; this module already does the cheap locate.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT = 300.0
# Ports commonly used by local ComfyUI.
_DEFAULT_PORTS: tuple[int, ...] = (8188, 8189, 8190, 8000)
# Shallow FS search limits (portable, bounded).
_MAX_WALK_DEPTH = 3
_MAX_WALK_DIRS = 400
_DIR_NAME_HINTS = frozenset({"comfyui", "comfy", "comfy-ui"})


# ---------------------------------------------------------------------------
# Config / env
# ---------------------------------------------------------------------------


def _remedy_home() -> Path:
    return Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()


def _load_comfy_config() -> dict[str, str]:
    """Optional keys from ~/.remedy/config.toml (no heavy imports on failure)."""
    out: dict[str, str] = {}
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        for key in ("comfyui_url", "comfyui_home", "comfyui_port"):
            val = cfg.get(key)
            if val is not None and str(val).strip():
                out[key] = str(val).strip()
    except Exception:
        pass
    # Side file users can drop without editing main config
    side = _remedy_home() / "comfyui.json"
    if side.is_file():
        try:
            data = json.loads(side.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("url", "base_url", "home", "port"):
                    if data.get(key):
                        out[f"side_{key}"] = str(data[key]).strip()
        except Exception:
            pass
    return out


def resolve_base_url(override: str | None = None) -> str:
    cfg = _load_comfy_config()
    port = (
        (cfg.get("comfyui_port") or cfg.get("side_port") or "").strip()
        or os.environ.get("COMFYUI_PORT", "").strip()
    )
    explicit = (
        (override or "").strip()
        or os.environ.get("COMFYUI_URL", "").strip()
        or os.environ.get("REMEDY_COMFYUI_URL", "").strip()
        or cfg.get("comfyui_url")
        or cfg.get("side_url")
        or cfg.get("side_base_url")
        or ""
    )
    if explicit:
        return explicit.rstrip("/")
    if port and port.isdigit():
        return f"http://127.0.0.1:{port}"
    return DEFAULT_BASE


def _explicit_homes() -> list[Path]:
    cfg = _load_comfy_config()
    raws = [
        os.environ.get("COMFYUI_HOME", "").strip(),
        os.environ.get("REMEDY_COMFYUI_HOME", "").strip(),
        cfg.get("comfyui_home") or "",
        cfg.get("side_home") or "",
    ]
    out: list[Path] = []
    for raw in raws:
        if not raw:
            continue
        out.append(Path(os.path.expandvars(raw)).expanduser())
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _request(
    method: str,
    path: str,
    *,
    base: str,
    data: bytes | None = None,
    timeout: float = 30.0,
) -> Any:
    url = f"{base.rstrip('/')}{path}"
    headers = {"Accept": "application/json", "User-Agent": "Remedy-ComfyUI-Tool/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype or raw[:1] in (b"{", b"["):
                return json.loads(raw.decode("utf-8"))
            return raw
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach ComfyUI at {base} ({e.reason}).") from e


def _probe_api(base: str, timeout: float = 2.0) -> dict[str, Any] | None:
    try:
        stats = _request("GET", "/system_stats", base=base, timeout=timeout)
        return {"base_url": base.rstrip("/"), "system_stats": stats}
    except Exception:
        return None


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover_api_endpoints() -> list[dict[str, Any]]:
    """Find live ComfyUI HTTP APIs on this machine (any user, any port habit)."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1) Configured / default URL first
    bases: list[str] = [resolve_base_url()]
    # 2) Common loopback hosts × ports
    for host in ("127.0.0.1", "localhost"):
        for port in _DEFAULT_PORTS:
            bases.append(f"http://{host}:{port}")

    for base in bases:
        key = base.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        # Skip TCP probe if not open (faster on closed ports)
        try:
            from urllib.parse import urlparse

            u = urlparse(base)
            host = u.hostname or "127.0.0.1"
            port = u.port or 80
            if not _port_open(host, port):
                continue
        except Exception:
            pass
        hit = _probe_api(base)
        if hit:
            found.append({"source": "http", **hit})
    return found


# ---------------------------------------------------------------------------
# Install discovery (portable)
# ---------------------------------------------------------------------------


def _python_for_install(root: Path) -> str:
    if sys.platform == "win32":
        for rel in (".venv/Scripts/python.exe", "venv/Scripts/python.exe", "python_embeded/python.exe"):
            p = root / rel
            if p.is_file():
                return str(p.resolve())
    else:
        for rel in (".venv/bin/python", "venv/bin/python"):
            p = root / rel
            if p.is_file():
                return str(p.resolve())
    return shutil.which("python") or shutil.which("python3") or "python"


def _start_hint(root: Path, python: str, port: int = 8188) -> str:
    if sys.platform == "win32":
        return (
            f'cd /d "{root}" && "{python}" main.py --listen 127.0.0.1 --port {port}'
        )
    return f'cd "{root}" && "{python}" main.py --listen 127.0.0.1 --port {port}'


def _install_record(root: Path, *, source: str) -> dict[str, str] | None:
    main = root / "main.py"
    if not main.is_file():
        # Nested layout: …/comfy/ComfyUI/main.py when root is …/comfy
        nested = root / "ComfyUI" / "main.py"
        if nested.is_file():
            root = nested.parent
            main = nested
        else:
            return None
    try:
        root_r = root.resolve()
    except OSError:
        root_r = root
    py = _python_for_install(root_r)
    return {
        "path": str(root_r),
        "main": str(main.resolve()) if main.exists() else str(root_r / "main.py"),
        "python": py,
        "start_hint": _start_hint(root_r, py),
        "source": source,
    }


def _candidate_static_paths() -> list[Path]:
    """Home-relative + a few OS-typical roots (no user-specific hardcoding)."""
    home = Path.home()
    names = ("ComfyUI", "comfyui", "comfy", "Comfy", "ComfyUI_windows_portable")
    bases = [
        home,
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "Projects",
        home / "dev",
        home / "src",
        home / "apps",
        home / "AppData" / "Local",
        home / "AppData" / "Local" / "Programs",
        home / ".local" / "share",
        home / "Library" / "Application Support",  # macOS
        Path("/opt"),
        Path("/usr/local"),
        Path("/Applications"),
    ]
    if sys.platform == "win32":
        for letter in ("C", "D", "E"):
            bases.append(Path(f"{letter}:/"))
            bases.append(Path(f"{letter}:/AI"))
            bases.append(Path(f"{letter}:/Apps"))
            bases.append(Path(f"{letter}:/Tools"))

    out: list[Path] = []
    for base in bases:
        if not base.exists():
            continue
        for name in names:
            out.append(base / name)
            out.append(base / name / "ComfyUI")
    return out


def _shallow_search_homes() -> list[Path]:
    """Bounded walk under home (and Desktop/Documents) for dirs named ComfyUI."""
    roots = [
        Path.home(),
        Path.home() / "Documents",
        Path.home() / "Desktop",
        Path.home() / "Downloads",
        Path.home() / "Projects",
        Path.home() / "dev",
    ]
    hits: list[Path] = []
    visited = 0
    for root in roots:
        if not root.is_dir():
            continue
        root_depth = len(root.parts)
        try:
            for dirpath, dirnames, _files in os.walk(root):
                visited += 1
                if visited > _MAX_WALK_DIRS:
                    return hits
                p = Path(dirpath)
                depth = len(p.parts) - root_depth
                if depth > _MAX_WALK_DEPTH:
                    dirnames[:] = []
                    continue
                # Prune heavy / irrelevant trees
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d.lower()
                    not in {
                        "node_modules",
                        ".git",
                        ".cache",
                        "__pycache__",
                        "appdata",
                        "windows",
                        "$recycle.bin",
                    }
                    and not d.startswith(".")
                ]
                name_l = p.name.lower().replace("_", "").replace("-", "")
                if name_l in {"comfyui", "comfy"} or "comfyui" in name_l:
                    hits.append(p)
                    if len(hits) >= 12:
                        return hits
        except OSError:
            continue
    return hits


def _process_install_paths() -> list[Path]:
    """Infer install dir from a running ComfyUI/python process (portable-ish)."""
    paths: list[Path] = []
    try:
        if sys.platform == "win32":
            # PowerShell: processes whose command line mentions ComfyUI main.py
            ps = (
                "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
                "Where-Object { $_.CommandLine -match 'ComfyUI|main\\.py' } | "
                "Select-Object -ExpandProperty CommandLine"
            )
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    ps,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            lines = (proc.stdout or "").splitlines()
        else:
            proc = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = [
                ln
                for ln in (proc.stdout or "").splitlines()
                if re.search(r"ComfyUI|main\.py", ln, re.I)
            ]
    except Exception:
        return paths

    for line in lines:
        # Paths containing ComfyUI segment
        for m in re.finditer(r'([A-Za-z]:\\[^\s"\']+|/[\w./-]+)', line):
            raw = m.group(1).rstrip("\"',")
            p = Path(raw)
            # If points at main.py, take parent
            if p.name.lower() == "main.py":
                paths.append(p.parent)
            elif "comfyui" in raw.lower():
                # Walk up to find main.py
                cur = p if p.is_dir() else p.parent
                for _ in range(4):
                    if (cur / "main.py").is_file():
                        paths.append(cur)
                        break
                    if cur.parent == cur:
                        break
                    cur = cur.parent
    return paths


def discover_installs() -> list[dict[str, str]]:
    """Locate ComfyUI installs on *this* machine without a full disk scan."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(root: Path, source: str) -> None:
        rec = _install_record(root, source=source)
        if not rec:
            return
        key = rec["path"].lower()
        if key in seen:
            return
        seen.add(key)
        found.append(rec)

    # 1) Explicit env / config
    for p in _explicit_homes():
        add(p, "config")

    # 2) Running processes
    for p in _process_install_paths():
        add(p, "process")

    # 3) Static home-relative candidates
    for p in _candidate_static_paths():
        add(p, "candidate")

    # 4) Bounded shallow search if still empty (or always fill a few more)
    if len(found) < 2:
        for p in _shallow_search_homes():
            add(p, "search")

    return found


# ---------------------------------------------------------------------------
# Public tool actions
# ---------------------------------------------------------------------------


def status(base_url: str | None = None) -> dict[str, Any]:
    """Ping ComfyUI HTTP API; include portable discovery if down."""
    base = resolve_base_url(base_url)
    try:
        stats = _request("GET", "/system_stats", base=base, timeout=5.0)
        return {
            "ok": True,
            "base_url": base,
            "system_stats": stats,
            "installs": discover_installs(),
            "live_endpoints": discover_api_endpoints(),
            "note": (
                "API is the health check. Use action=generate to make images. "
                "Do not list_dir the whole disk looking for ComfyUI."
            ),
        }
    except Exception as e:
        installs = discover_installs()
        endpoints = discover_api_endpoints()
        hints: list[str] = [str(e)]
        if endpoints:
            hints.append("Other live ComfyUI endpoints detected:")
            for ep in endpoints:
                hints.append(f"  - {ep.get('base_url')}")
        if installs:
            hints.append("Install(s) found on this machine:")
            for inst in installs[:5]:
                hints.append(f"  - {inst['path']} (via {inst.get('source', '?')})")
                hints.append(f"    Start: {inst['start_hint']}")
        else:
            hints.append(
                "No install found. Set COMFYUI_HOME (folder with main.py) and/or "
                "COMFYUI_URL, or add comfyui_home / comfyui_url to ~/.remedy/config.toml, "
                "or create ~/.remedy/comfyui.json with {\"home\": \"...\", \"url\": \"...\"}."
            )
        return {
            "ok": False,
            "base_url": base,
            "error": str(e),
            "installs": installs,
            "live_endpoints": endpoints,
            "hint": "\n".join(hints),
            "note": (
                "Never use list_dir/bash to hunt for ComfyUI — use action=locate/status."
            ),
        }


def locate() -> dict[str, Any]:
    """Portable discovery: live APIs + install dirs + how to start."""
    installs = discover_installs()
    endpoints = discover_api_endpoints()
    base = resolve_base_url()
    primary = _probe_api(base)
    return {
        "ok": bool(endpoints or installs or primary),
        "api_reachable": primary is not None or bool(endpoints),
        "base_url": base,
        "preferred_endpoint": (primary or (endpoints[0] if endpoints else {})).get(
            "base_url", base
        ),
        "live_endpoints": endpoints,
        "installs": installs,
        "config_keys": {
            "env": ["COMFYUI_URL", "COMFYUI_HOME", "COMFYUI_PORT", "REMEDY_COMFYUI_URL", "REMEDY_COMFYUI_HOME"],
            "config.toml": ["comfyui_url", "comfyui_home", "comfyui_port"],
            "file": str(_remedy_home() / "comfyui.json"),
        },
        "note": (
            "Discovery is automatic for any machine. "
            "Override with env/config when installs live in unusual places."
        ),
    }


def _default_workflow_path() -> Path:
    pkg = Path(__file__).resolve().parents[1]
    return (
        pkg
        / "bundled_skills"
        / "comfyui"
        / "scripts"
        / "workflows"
        / "txt2img_flux2_klein.json"
    )


def build_txt2img_workflow(
    prompt: str,
    *,
    negative: str = "",
    width: int = 512,
    height: int = 512,
    steps: int = 16,
    seed: int | None = None,
    workflow_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(workflow_path) if workflow_path else _default_workflow_path()
    if not path.is_file():
        raise FileNotFoundError(f"Workflow template not found: {path}")
    wf = json.loads(path.read_text(encoding="utf-8"))
    wf.pop("_meta", None)
    if "6" in wf and isinstance(wf["6"], dict):
        wf["6"].setdefault("inputs", {})["text"] = prompt
    if "7" in wf and isinstance(wf["7"], dict):
        wf["7"].setdefault("inputs", {})["text"] = negative
    if "5" in wf and isinstance(wf["5"], dict):
        wf["5"].setdefault("inputs", {})["width"] = int(width)
        wf["5"].setdefault("inputs", {})["height"] = int(height)
    if "8" in wf and isinstance(wf["8"], dict):
        wf["8"].setdefault("inputs", {})["steps"] = int(steps)
        wf["8"]["inputs"]["seed"] = (
            int(seed) if seed is not None else random.randint(1, 2**31 - 1)
        )
    return wf


def queue_prompt(workflow: dict[str, Any], *, base_url: str | None = None) -> str:
    base = resolve_base_url(base_url)
    # Auto-pick a live endpoint if default is down
    if _probe_api(base) is None:
        for ep in discover_api_endpoints():
            base = str(ep.get("base_url") or base)
            break
    if isinstance(workflow, dict) and "prompt" in workflow and len(workflow) <= 3:
        payload = workflow
    else:
        payload = {"prompt": workflow}
    result = _request(
        "POST",
        "/prompt",
        base=base,
        data=json.dumps(payload).encode("utf-8"),
        timeout=60.0,
    )
    if not isinstance(result, dict) or not result.get("prompt_id"):
        raise RuntimeError(f"Unexpected queue response: {result!r}")
    if result.get("node_errors"):
        raise RuntimeError(f"Workflow node errors: {result['node_errors']}")
    return str(result["prompt_id"])


def wait_prompt(
    prompt_id: str,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = 1.5,
) -> dict[str, Any]:
    base = resolve_base_url(base_url)
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        hist = _request("GET", f"/history/{prompt_id}", base=base, timeout=15.0)
        if isinstance(hist, dict) and prompt_id in hist:
            return hist[prompt_id]
        time.sleep(interval)
    raise TimeoutError(f"ComfyUI timed out after {timeout}s (prompt_id={prompt_id})")


def _iter_images(history_entry: dict[str, Any]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    outputs = history_entry.get("outputs") or {}
    if not isinstance(outputs, dict):
        return images
    for _nid, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for img in node_out.get("images") or []:
            if isinstance(img, dict) and img.get("filename"):
                images.append(
                    {
                        "filename": str(img.get("filename") or ""),
                        "subfolder": str(img.get("subfolder") or ""),
                        "type": str(img.get("type") or "output"),
                    }
                )
    return images


def download_images(
    prompt_id: str,
    out_dir: Path,
    *,
    base_url: str | None = None,
    history_entry: dict[str, Any] | None = None,
) -> list[Path]:
    base = resolve_base_url(base_url)
    if history_entry is None:
        hist = _request("GET", f"/history/{prompt_id}", base=base, timeout=15.0)
        if not isinstance(hist, dict) or prompt_id not in hist:
            raise RuntimeError(f"No history for prompt_id={prompt_id}")
        history_entry = hist[prompt_id]
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for img in _iter_images(history_entry):
        qs = urllib.parse.urlencode(img)
        data = _request("GET", f"/view?{qs}", base=base, timeout=120.0)
        if not isinstance(data, (bytes, bytearray)):
            continue
        dest = out_dir / img["filename"]
        dest.write_bytes(data)
        saved.append(dest.resolve())
    return saved


def generate_image(
    prompt: str,
    *,
    base_url: str | None = None,
    out_dir: Path | None = None,
    width: int = 512,
    height: int = 512,
    steps: int = 16,
    seed: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    base = resolve_base_url(base_url)
    # Prefer a live endpoint when default is offline
    if _probe_api(base) is None:
        eps = discover_api_endpoints()
        if eps:
            base = str(eps[0].get("base_url") or base)
        else:
            loc = locate()
            raise RuntimeError(
                loc.get("note")
                or "ComfyUI API not reachable. Start it or set COMFYUI_URL."
            )
    wf = build_txt2img_workflow(
        prompt, width=width, height=height, steps=steps, seed=seed
    )
    prompt_id = queue_prompt(wf, base_url=base)
    entry = wait_prompt(prompt_id, base_url=base, timeout=timeout)
    dest = out_dir or (Path.home() / ".remedy" / "comfy_out")
    paths = download_images(prompt_id, dest, base_url=base, history_entry=entry)
    return {
        "ok": True,
        "base_url": base,
        "prompt_id": prompt_id,
        "prompt": prompt,
        "paths": [str(p) for p in paths],
        "seed": wf.get("8", {}).get("inputs", {}).get("seed"),
    }


def attach_image_to_session(
    session_id: str,
    image_path: Path,
    *,
    home_dir: str | Path | None = None,
) -> dict[str, Any]:
    from remedy.interfaces.attachments import save_upload

    data = Path(image_path).read_bytes()
    meta = save_upload(
        session_id=session_id,
        filename=Path(image_path).name,
        data=data,
        content_type="image/png",
        home_dir=home_dir,
    )
    name = meta.get("name") or Path(image_path).name
    meta["view_url"] = (
        f"http://127.0.0.1:7400/api/sessions/{session_id}/attachments/{name}"
    )
    return meta


def markdown_for_image(
    meta: dict[str, Any],
    caption: str = "",
    *,
    embed_data_uri: bool = True,
    max_embed_bytes: int = 900_000,
) -> str:
    """Markdown for chat: session URL + optional data-URI so the bubble always shows."""
    import base64

    url = meta.get("view_url") or ""
    path = meta.get("path") or ""
    name = meta.get("name") or "image.png"
    cap = (caption or name).replace("\n", " ")[:120]
    lines: list[str] = []
    # Prefer inlined data URI for WebView reliability (CSP allows data:).
    if embed_data_uri and path and Path(path).is_file():
        try:
            raw = Path(path).read_bytes()
            if len(raw) <= max_embed_bytes:
                b64 = base64.b64encode(raw).decode("ascii")
                mime = meta.get("mime") or "image/png"
                lines.append(f"![{cap}](data:{mime};base64,{b64})")
        except OSError:
            pass
    if not lines and url:
        lines.append(f"![{cap}]({url})")
    lines.append(f"**{cap}**")
    if path:
        lines.append(f"Saved: `{path}`")
    if url:
        lines.append(f"[Open in session]({url})")
    return "\n\n".join(lines)
