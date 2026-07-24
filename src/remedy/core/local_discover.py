"""Portable local discovery for skills, tools, and ambient services.

Any Remedy install on any machine should *just work*: find local HTTP services,
install dirs, and binaries without the agent thrashing ``list_dir`` / ``bash``
across the whole disk.

Discovery order (cheapest → broader, always bounded):
  1. Explicit env + ``~/.remedy`` config / side JSON
  2. Live loopback HTTP probes (ports from the skill or defaults)
  3. Running process command lines (optional)
  4. Static home-relative candidates + shallow home walk (depth-limited)

Skills declare needs in SKILL.md frontmatter under ``local:`` (see
:func:`parse_skill_local_spec`). Built-in tools (e.g. ComfyUI) reuse the same
helpers so behavior stays consistent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Spec model (from skill frontmatter or built-ins)
# ---------------------------------------------------------------------------


@dataclass
class HttpServiceSpec:
    """A local HTTP service a skill depends on."""

    id: str
    ports: list[int] = field(default_factory=lambda: [8080])
    path: str = "/"  # health path
    hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])
    env_url: list[str] = field(default_factory=list)
    config_url_keys: list[str] = field(default_factory=list)
    env_home: list[str] = field(default_factory=list)
    config_home_keys: list[str] = field(default_factory=list)
    dir_names: list[str] = field(default_factory=list)
    entry_files: list[str] = field(default_factory=lambda: ["main.py"])
    start_template: str = ""  # optional; {root} {python} {port}


@dataclass
class BinarySpec:
    id: str
    names: list[str] = field(default_factory=list)  # PATH basenames
    env: list[str] = field(default_factory=list)


@dataclass
class LocalNeedSpec:
    """Everything a skill (or built-in) needs from the local machine."""

    skill: str = ""
    services: list[HttpServiceSpec] = field(default_factory=list)
    binaries: list[BinarySpec] = field(default_factory=list)


def parse_skill_local_spec(name: str, frontmatter: dict[str, Any] | None) -> LocalNeedSpec | None:
    """Parse optional ``local:`` block from SKILL.md YAML frontmatter."""
    if not frontmatter:
        return None
    raw = frontmatter.get("local")
    if not raw or not isinstance(raw, dict):
        return None
    services: list[HttpServiceSpec] = []
    for item in raw.get("services") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        ports = item.get("ports") or [item.get("port") or 8080]
        ports = [int(p) for p in ports if str(p).isdigit() or isinstance(p, int)]
        services.append(
            HttpServiceSpec(
                id=str(item["id"]),
                ports=ports or [8080],
                path=str(item.get("path") or item.get("health") or "/"),
                hosts=list(item.get("hosts") or ["127.0.0.1", "localhost"]),
                env_url=list(item.get("env_url") or item.get("env") or []),
                config_url_keys=list(item.get("config_url") or []),
                env_home=list(item.get("env_home") or []),
                config_home_keys=list(item.get("config_home") or []),
                dir_names=list(item.get("dir_names") or item.get("names") or []),
                entry_files=list(item.get("entry") or item.get("entry_files") or ["main.py"]),
                start_template=str(item.get("start") or item.get("start_template") or ""),
            )
        )
    binaries: list[BinarySpec] = []
    for item in raw.get("binaries") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        binaries.append(
            BinarySpec(
                id=str(item["id"]),
                names=list(item.get("names") or [item["id"]]),
                env=list(item.get("env") or []),
            )
        )
    if not services and not binaries:
        return None
    return LocalNeedSpec(skill=name, services=services, binaries=binaries)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def remedy_home() -> Path:
    return Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()


def _load_remedy_config() -> dict[str, Any]:
    try:
        from remedy.interfaces.config import load_config

        return dict(load_config() or {})
    except Exception:
        return {}


def _side_json(name: str) -> dict[str, Any]:
    path = remedy_home() / f"{name}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first_env(keys: list[str]) -> str:
    for k in keys:
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return ""


def _first_config(cfg: dict[str, Any], keys: list[str]) -> str:
    for k in keys:
        v = cfg.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


# ---------------------------------------------------------------------------
# HTTP / ports
# ---------------------------------------------------------------------------


def port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_get_json(url: str, timeout: float = 2.5) -> Any | None:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Remedy-LocalDiscover/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {"_empty": True, "status": getattr(resp, "status", 200)}
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {"_text": raw[:200].decode("utf-8", errors="replace")}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def probe_http_service(spec: HttpServiceSpec) -> list[dict[str, Any]]:
    """Return live endpoints matching the service spec."""
    cfg = _load_remedy_config()
    side = _side_json(spec.id)
    urls: list[str] = []

    for key in spec.env_url:
        v = os.environ.get(key, "").strip()
        if v:
            urls.append(v.rstrip("/"))
    for key in spec.config_url_keys:
        v = _first_config(cfg, [key])
        if v:
            urls.append(v.rstrip("/"))
    for key in ("url", "base_url"):
        if side.get(key):
            urls.append(str(side[key]).rstrip("/"))

    for host in spec.hosts:
        for port in spec.ports:
            urls.append(f"http://{host}:{port}")

    seen: set[str] = set()
    hits: list[dict[str, Any]] = []
    health = spec.path if spec.path.startswith("/") else f"/{spec.path}"
    for base in urls:
        key = base.lower()
        if key in seen:
            continue
        seen.add(key)
        # Fast TCP skip
        try:
            from urllib.parse import urlparse

            u = urlparse(base if "://" in base else f"http://{base}")
            host = u.hostname or "127.0.0.1"
            port = u.port or 80
            if not port_open(host, port):
                continue
        except Exception:
            pass
        body = http_get_json(f"{base.rstrip('/')}{health}")
        if body is not None:
            hits.append(
                {
                    "id": spec.id,
                    "base_url": base.rstrip("/"),
                    "health_path": health,
                    "ok": True,
                    "sample": body if not isinstance(body, dict) or len(json.dumps(body)) < 800 else {"_truncated": True},
                }
            )
    return hits


# ---------------------------------------------------------------------------
# Install / binary discovery
# ---------------------------------------------------------------------------


def _python_in(root: Path) -> str:
    if sys.platform == "win32":
        for rel in (
            ".venv/Scripts/python.exe",
            "venv/Scripts/python.exe",
            "python_embeded/python.exe",
        ):
            p = root / rel
            if p.is_file():
                return str(p.resolve())
    else:
        for rel in (".venv/bin/python", "venv/bin/python"):
            p = root / rel
            if p.is_file():
                return str(p.resolve())
    return shutil.which("python") or shutil.which("python3") or "python"


def _has_entry(root: Path, entry_files: list[str]) -> Path | None:
    for name in entry_files:
        p = root / name
        if p.is_file():
            return p
        nested = root / "ComfyUI" / name  # common nesting; harmless for others
        if nested.is_file():
            return nested
    return None


def _start_hint(root: Path, entry: Path, python: str, port: int, template: str) -> str:
    if template:
        return template.format(root=root, python=python, port=port, entry=entry)
    if entry.suffix == ".py":
        if sys.platform == "win32":
            return f'cd /d "{root}" && "{python}" "{entry.name}" --listen 127.0.0.1 --port {port}'
        return f'cd "{root}" && "{python}" "{entry.name}" --listen 127.0.0.1 --port {port}'
    return str(entry)


def discover_install_dirs(spec: HttpServiceSpec) -> list[dict[str, str]]:
    """Find install directories for a service (bounded, portable)."""
    cfg = _load_remedy_config()
    side = _side_json(spec.id)
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(root: Path, source: str) -> None:
        entry = _has_entry(root, spec.entry_files)
        if entry is None:
            # parent folder that nests project
            if (root / spec.dir_names[0] if spec.dir_names else root).exists():
                pass
            return
        # If entry is nested …/ComfyUI/main.py, root should be that parent
        if entry.parent != root and entry.parent.name:
            root = entry.parent
        try:
            key = str(root.resolve()).lower()
        except OSError:
            key = str(root).lower()
        if key in seen:
            return
        seen.add(key)
        py = _python_in(root)
        port = spec.ports[0] if spec.ports else 8080
        found.append(
            {
                "id": spec.id,
                "path": str(root.resolve()) if root.exists() else str(root),
                "entry": str(entry.resolve()) if entry.exists() else str(entry),
                "python": py,
                "source": source,
                "start_hint": _start_hint(root, entry, py, port, spec.start_template),
            }
        )

    # Explicit homes
    homes: list[str] = []
    homes.append(_first_env(spec.env_home))
    homes.append(_first_config(cfg, spec.config_home_keys))
    if side.get("home"):
        homes.append(str(side["home"]))
    for h in homes:
        if h:
            add(Path(os.path.expandvars(h)).expanduser(), "config")

    # Process scan
    for p in _process_paths(spec):
        add(p, "process")

    # Static candidates under home + common roots
    names = spec.dir_names or [spec.id, spec.id.replace("-", ""), spec.id.title()]
    home = Path.home()
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
        home / "Library" / "Application Support",
        Path("/opt"),
        Path("/usr/local"),
    ]
    if sys.platform == "win32":
        for letter in ("C", "D"):
            bases.append(Path(f"{letter}:/"))
            bases.append(Path(f"{letter}:/AI"))
            bases.append(Path(f"{letter}:/Apps"))

    for base in bases:
        if not base.exists():
            continue
        for name in names:
            add(base / name, "candidate")
            add(base / name / name, "candidate")

    # Shallow walk if still thin
    if len(found) < 1:
        for p in _shallow_named_dirs(names):
            add(p, "search")

    return found


def _process_paths(spec: HttpServiceSpec) -> list[Path]:
    needles = [spec.id] + list(spec.dir_names) + list(spec.entry_files)
    needles = [n for n in needles if n]
    pattern = "|".join(re.escape(n) for n in needles)
    paths: list[Path] = []
    try:
        if sys.platform == "win32":
            ps = (
                "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
                f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
                "Select-Object -ExpandProperty CommandLine"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            lines = (proc.stdout or "").splitlines()
        else:
            proc = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            lines = [
                ln
                for ln in (proc.stdout or "").splitlines()
                if re.search(pattern, ln, re.I)
            ]
    except Exception:
        return paths

    for line in lines:
        for m in re.finditer(r'([A-Za-z]:\\[^\s"\']+|/[\w./-]+)', line):
            raw = m.group(1).rstrip("\"',")
            p = Path(raw)
            if p.name in spec.entry_files:
                paths.append(p.parent)
            else:
                cur = p if p.is_dir() else p.parent
                for _ in range(5):
                    if _has_entry(cur, spec.entry_files):
                        paths.append(cur)
                        break
                    if cur.parent == cur:
                        break
                    cur = cur.parent
    return paths


def _shallow_named_dirs(names: list[str], *, max_depth: int = 3, max_dirs: int = 400) -> list[Path]:
    want = {n.lower().replace("_", "").replace("-", "") for n in names}
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
            for dirpath, dirnames, _ in os.walk(root):
                visited += 1
                if visited > max_dirs:
                    return hits
                p = Path(dirpath)
                depth = len(p.parts) - root_depth
                if depth > max_depth:
                    dirnames[:] = []
                    continue
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d.lower()
                    not in {
                        "node_modules",
                        ".git",
                        ".cache",
                        "__pycache__",
                        "windows",
                        "$recycle.bin",
                    }
                    and not d.startswith(".")
                ]
                key = p.name.lower().replace("_", "").replace("-", "")
                if key in want or any(w in key for w in want if len(w) > 3):
                    hits.append(p)
                    if len(hits) >= 12:
                        return hits
        except OSError:
            continue
    return hits


def discover_binaries(spec: BinarySpec) -> dict[str, Any]:
    for key in spec.env:
        v = os.environ.get(key, "").strip()
        if v and Path(v).is_file():
            return {"id": spec.id, "path": v, "source": "env", "ok": True}
    for name in spec.names:
        found = shutil.which(name)
        if found:
            return {"id": spec.id, "path": found, "source": "path", "ok": True}
    return {"id": spec.id, "path": "", "source": "", "ok": False, "tried": list(spec.names)}


# ---------------------------------------------------------------------------
# Skill registry integration
# ---------------------------------------------------------------------------


def collect_skill_local_specs(skills: list[Any] | None = None) -> list[LocalNeedSpec]:
    """Build LocalNeedSpec list from loaded Skill objects or registry."""
    specs: list[LocalNeedSpec] = []
    if skills is None:
        return specs
    for skill in skills:
        try:
            manifest = getattr(skill, "manifest", None)
            if manifest is None:
                continue
            name = getattr(manifest, "name", "") or ""
            raw = getattr(manifest, "raw_frontmatter", None) or {}
            # Also allow metadata.local
            if "local" not in raw and isinstance(getattr(manifest, "metadata", None), dict):
                raw = {**raw, "local": manifest.metadata.get("local")}
            parsed = parse_skill_local_spec(name, raw if isinstance(raw, dict) else {})
            if parsed:
                specs.append(parsed)
        except Exception:
            continue
    return specs


def builtin_service_specs() -> list[LocalNeedSpec]:
    """Well-known local services Remedy can always try to find."""
    return [
        LocalNeedSpec(
            skill="comfyui",
            services=[
                HttpServiceSpec(
                    id="comfyui",
                    ports=[8188, 8189, 8190, 8000],
                    path="/system_stats",
                    env_url=["COMFYUI_URL", "REMEDY_COMFYUI_URL"],
                    config_url_keys=["comfyui_url"],
                    env_home=["COMFYUI_HOME", "REMEDY_COMFYUI_HOME"],
                    config_home_keys=["comfyui_home"],
                    dir_names=["ComfyUI", "comfyui", "comfy", "ComfyUI_windows_portable"],
                    entry_files=["main.py"],
                )
            ],
        ),
        LocalNeedSpec(
            skill="ollama",
            services=[
                HttpServiceSpec(
                    id="ollama",
                    ports=[11434],
                    path="/api/tags",
                    env_url=["OLLAMA_HOST", "OLLAMA_URL"],
                    config_url_keys=["ollama_url"],
                    dir_names=["ollama", ".ollama"],
                    entry_files=[],  # binary-based
                )
            ],
            binaries=[BinarySpec(id="ollama", names=["ollama"], env=["OLLAMA_BIN"])],
        ),
    ]


def discover_all(
    *,
    skill_specs: list[LocalNeedSpec] | None = None,
    include_builtins: bool = True,
) -> dict[str, Any]:
    """Run portable discovery for all known local needs."""
    specs = list(skill_specs or [])
    if include_builtins:
        # Prefer skill-declared specs; fill builtins for missing ids
        have = {s.skill for s in specs}
        for b in builtin_service_specs():
            if b.skill not in have:
                specs.append(b)

    services_out: list[dict[str, Any]] = []
    binaries_out: list[dict[str, Any]] = []
    installs_out: list[dict[str, str]] = []

    for spec in specs:
        for svc in spec.services:
            endpoints = probe_http_service(svc)
            installs = discover_install_dirs(svc) if svc.entry_files or svc.dir_names else []
            services_out.append(
                {
                    "skill": spec.skill,
                    "id": svc.id,
                    "ok": bool(endpoints),
                    "endpoints": endpoints,
                    "installs": installs,
                }
            )
            installs_out.extend(installs)
        for binary in spec.binaries:
            binaries_out.append({**discover_binaries(binary), "skill": spec.skill})

    return {
        "ok": any(s.get("ok") for s in services_out) or any(b.get("ok") for b in binaries_out),
        "services": services_out,
        "binaries": binaries_out,
        "installs": installs_out,
        "note": (
            "Portable local discovery — use this instead of list_dir/bash disk hunts. "
            "Override unusual layouts with env vars or ~/.remedy/<service>.json."
        ),
    }


def discover_one(service_id: str, *, skill_specs: list[LocalNeedSpec] | None = None) -> dict[str, Any]:
    """Discover a single service id (e.g. comfyui, ollama)."""
    all_specs = list(skill_specs or []) + builtin_service_specs()
    matched: list[LocalNeedSpec] = []
    for spec in all_specs:
        if spec.skill == service_id or any(s.id == service_id for s in spec.services):
            # Filter to that service only
            svcs = [s for s in spec.services if s.id == service_id or spec.skill == service_id]
            bins = [b for b in spec.binaries if b.id == service_id or spec.skill == service_id]
            matched.append(LocalNeedSpec(skill=spec.skill, services=svcs or list(spec.services), binaries=bins))
    if not matched:
        # Ad-hoc: treat as HTTP on common ports if digits given
        return {"ok": False, "error": f"Unknown local service: {service_id}", "services": []}
    return discover_all(skill_specs=matched, include_builtins=False)
