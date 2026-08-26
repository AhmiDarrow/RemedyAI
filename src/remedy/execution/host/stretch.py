"""First-home stretch — map hardware, tools, rooms, and doors of this PC.

On first install (and when the map goes stale) Remedy looks around once:
PATH tools, RAM/GPU, user folders, a few local ports. No disk walk, no
process listing, no secrets. Result lives in ``~/.remedy/host/home.json``.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import socket
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic

logger = logging.getLogger(__name__)

_CENSUS_REL = Path("host") / "home.json"
_STALE_DAYS = 14

# Agent-useful binaries only — shutil.which, never a filesystem crawl.
_TOOL_NAMES: tuple[str, ...] = (
    "python",
    "py",
    "git",
    "node",
    "npm",
    "npx",
    "uv",
    "pip",
    "pipx",
    "cargo",
    "rustc",
    "go",
    "gcc",
    "clang",
    "cmake",
    "docker",
    "rg",
    "curl",
    "pwsh",
    "code",
    "gh",
    "ffmpeg",
    "conda",
    "winget",
    "choco",
    "scoop",
    "bun",
    "pnpm",
    "yarn",
    "java",
    "amd-smi",
    "rocm-smi",
    "vulkaninfo",
)

_ROOM_KEYS: tuple[tuple[str, str], ...] = (
    ("profile", ""),
    ("desktop", "Desktop"),
    ("documents", "Documents"),
    ("downloads", "Downloads"),
    ("pictures", "Pictures"),
)

_WORK_HINTS: tuple[tuple[str, ...], ...] = (
    ("Documents", "Remedy Projects"),
    ("Projects",),
    ("dev",),
    ("src",),
)

_DOORS: tuple[tuple[str, int], ...] = (
    ("remedy", 7400),
    ("rmb", 8787),
    ("vision", 8740),
    ("ollama", 11434),
    ("comfyui", 8188),
)

_SECRET_KEY_BITS = ("key", "token", "secret", "password", "auth", "cookie")


@dataclass
class HomeCensus:
    stretched_at: str = ""
    os_name: str = ""
    os_release: str = ""
    arch: str = ""
    hostname: str = ""
    cpu_count: int = 0
    ram_total_mb: int = 0
    ram_avail_mb: int = 0
    gpu_name: str = ""
    vram_total_mb: int = 0
    nvidia: bool = False
    gpu_vendor: str = ""
    gpus: list[dict[str, Any]] = field(default_factory=list)
    disk_home_free_gb: float = 0.0
    tools: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    rooms: dict[str, str] = field(default_factory=dict)
    work_rooms: dict[str, str] = field(default_factory=dict)
    doors: dict[str, bool] = field(default_factory=dict)
    host: str = "cmd"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HomeCensus:
        if not isinstance(data, dict):
            return cls()
        tools_raw = data.get("tools") or {}
        rooms_raw = data.get("rooms") or {}
        work_raw = data.get("work_rooms") or {}
        doors_raw = data.get("doors") or {}
        missing_raw = data.get("missing") or []
        return cls(
            stretched_at=str(data.get("stretched_at") or ""),
            os_name=str(data.get("os_name") or ""),
            os_release=str(data.get("os_release") or ""),
            arch=str(data.get("arch") or ""),
            hostname=str(data.get("hostname") or "")[:80],
            cpu_count=int(data.get("cpu_count") or 0),
            ram_total_mb=int(data.get("ram_total_mb") or 0),
            ram_avail_mb=int(data.get("ram_avail_mb") or 0),
            gpu_name=str(data.get("gpu_name") or "")[:80],
            vram_total_mb=int(data.get("vram_total_mb") or 0),
            nvidia=bool(data.get("nvidia")),
            gpu_vendor=str(data.get("gpu_vendor") or ""),
            gpus=_safe_gpu_list(data.get("gpus")),
            disk_home_free_gb=float(data.get("disk_home_free_gb") or 0.0),
            tools=_safe_str_map(tools_raw),
            missing=[str(m) for m in missing_raw if str(m)][:40],
            rooms=_safe_str_map(rooms_raw),
            work_rooms=_safe_str_map(work_raw),
            doors={
                str(k): bool(v)
                for k, v in doors_raw.items()
                if isinstance(k, str) and not _looks_secret(k)
            },
            host=str(data.get("host") or "cmd"),
        )


def census_path(home: str | Path | None = None) -> Path:
    return _home(home) / _CENSUS_REL


def load_census(home: str | Path | None = None) -> HomeCensus | None:
    path = census_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return HomeCensus.from_dict(raw)


def save_census(census: HomeCensus, home: str | Path | None = None) -> Path:
    path = census_path(home)
    write_json_atomic(path, census.to_dict())
    return path


def needs_stretch(home: str | Path | None = None, *, stale_days: int = _STALE_DAYS) -> bool:
    c = load_census(home)
    if c is None or not c.stretched_at:
        return True
    try:
        ts = datetime.fromisoformat(c.stretched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    age = datetime.now(UTC) - ts.astimezone(UTC)
    return age.days >= max(1, int(stale_days))


def stretch_home(home: str | Path | None = None, *, force: bool = False) -> HomeCensus:
    """Probe this PC and persist the census. Cheap: which + hardware + ports."""
    if not force:
        existing = load_census(home)
        if existing is not None and not needs_stretch(home):
            return existing
    census = _probe(home)
    save_census(census, home)
    try:
        from remedy.execution.host.dialect import probe_host_dialect

        probe_host_dialect(home=home, persist=True)
    except Exception:
        logger.debug("dialect persist during stretch failed", exc_info=True)
    logger.info(
        "Home stretch: %s · %d tools · gpu=%s",
        census.os_name,
        len(census.tools),
        census.gpu_name or "none",
    )
    return census


def ensure_home_stretch(
    home: str | Path | None = None,
    *,
    force: bool = False,
    background: bool = True,
) -> HomeCensus | None:
    """Stretch if needed. Default: daemon thread so first serve stays snappy."""
    if not force and not needs_stretch(home):
        return load_census(home)
    if background:
        threading.Thread(
            target=_stretch_safe,
            args=(home, force),
            name="remedy-home-stretch",
            daemon=True,
        ).start()
        return load_census(home)
    return _stretch_safe(home, force)


def format_home_line(
    census: HomeCensus | None = None,
    home: str | Path | None = None,
) -> str:
    """One compact inject line for the workspace block."""
    c = census if census is not None else load_census(home)
    if c is None or not c.stretched_at:
        try:
            from remedy.execution.host.dialect import format_dialect_line

            return format_dialect_line(home=home)
        except Exception:
            return ""
    bits: list[str] = [f"This home: {c.os_name or platform.system()}"]
    if c.ram_total_mb:
        bits.append(f"{_gb(c.ram_total_mb)} RAM")
    if c.gpu_name:
        gpu = c.gpu_name
        if c.vram_total_mb:
            gpu = f"{gpu} {_gb(c.vram_total_mb)}"
        bits.append(gpu)
    if c.cpu_count:
        bits.append(f"{c.cpu_count} CPU")
    present = [n for n in ("python", "git", "uv", "node", "rg", "pwsh", "cargo") if n in c.tools]
    if present:
        bits.append(" ".join(present))
    open_doors = [k for k, v in c.doors.items() if v]
    if open_doors:
        bits.append("doors: " + ",".join(open_doors))
    bits.append(f"host={c.host or 'cmd'}")
    bits.append("prefer host_run / host_mkdir / host_script")
    return " · ".join(bits)


def format_home_whoami(
    census: HomeCensus | None = None,
    home: str | Path | None = None,
) -> str:
    """Longer block for /whoami and /stretch."""
    c = census if census is not None else load_census(home)
    if c is None or not c.stretched_at:
        return "_This home has not been stretched yet. `/stretch` maps hardware and tools._"
    lines = ["**This home** (stretched " + c.stretched_at.replace("T", " ")[:16] + " UTC)"]
    hw = []
    if c.os_name:
        hw.append(f"{c.os_name} {c.os_release}".strip())
    if c.arch:
        hw.append(c.arch)
    if c.cpu_count:
        hw.append(f"{c.cpu_count} CPU")
    if c.ram_total_mb:
        hw.append(f"{_gb(c.ram_total_mb)} RAM")
    if c.gpu_name:
        extra = f" ({_gb(c.vram_total_mb)})" if c.vram_total_mb else ""
        hw.append(c.gpu_name + extra)
    elif c.gpus:
        names = [str(g.get("name") or "GPU") for g in c.gpus[:3]]
        hw.append(" + ".join(names))
    if hw:
        lines.append("- **Hardware:** " + " · ".join(hw))
    if c.tools:
        shown = [f"{k}" for k in list(c.tools)[:16]]
        lines.append("- **Tools:** " + ", ".join(shown))
    if c.missing:
        lines.append("- **Not on PATH:** " + ", ".join(c.missing[:12]))
    rooms = [k for k, p in c.rooms.items() if p]
    if rooms:
        lines.append("- **Rooms:** " + ", ".join(rooms))
    if c.work_rooms:
        lines.append("- **Work rooms:** " + ", ".join(c.work_rooms))
    if c.doors:
        open_d = [k for k, v in c.doors.items() if v]
        shut = [k for k, v in c.doors.items() if not v]
        door_s = []
        if open_d:
            door_s.append("open " + ", ".join(open_d))
        if shut:
            door_s.append("quiet " + ", ".join(shut))
        lines.append("- **Doors:** " + " · ".join(door_s))
    lines.append(
        "_Re-stretch anytime with_ `/stretch` _after you install tools. "
        "No disk crawl — PATH, hardware, and a few local ports only._"
    )
    return "\n".join(lines)


def _stretch_safe(home: str | Path | None, force: bool) -> HomeCensus | None:
    try:
        return stretch_home(home, force=force)
    except Exception:
        logger.exception("home stretch failed")
        return None


def _probe(home: str | Path | None) -> HomeCensus:
    tools: dict[str, str] = {}
    missing: list[str] = []
    for name in _TOOL_NAMES:
        found = shutil.which(name)
        if found:
            tools[name] = found
        else:
            missing.append(name)
    # Real CPython only — frozen Desktop's sys.executable is the sidecar.
    if "python" not in tools:
        try:
            from remedy.core.build_python import host_python_executable

            found = host_python_executable()
        except Exception:
            found = ""
        if found:
            tools["python"] = found
            missing = [m for m in missing if m != "python"]

    hw_gpu = ""
    hw_vram = 0
    hw_ram_t = 0
    hw_ram_a = 0
    nvidia = False
    gpu_vendor = ""
    gpus: list[dict[str, Any]] = []
    cpu = os.cpu_count() or 0
    try:
        from remedy.runtime.gpu_probe import probe_gpus
        from remedy.runtime.rmb.autofit import probe_hardware

        hw = probe_hardware()
        nvidia = bool(hw.nvidia)
        hw_vram = int(hw.vram_total_mb or 0)
        hw_gpu = str(hw.gpu_name or "")[:80]
        hw_ram_t = int(hw.ram_total_mb or 0)
        hw_ram_a = int(hw.ram_avail_mb or 0)
        cpu = int(hw.cpu_count or cpu)
        gpu_vendor = str(hw.gpu_vendor or ("nvidia" if nvidia else ""))
        snap = probe_gpus()
        gpus = [d.to_public() for d in snap.devices]
        if not hw_gpu and snap.primary:
            hw_gpu = snap.primary.name[:80]
            hw_vram = snap.primary.vram_total_mb
            gpu_vendor = snap.primary.vendor
            nvidia = gpu_vendor == "nvidia"
    except Exception:
        logger.debug("hardware probe failed", exc_info=True)

    profile = Path.home()
    rooms: dict[str, str] = {}
    for key, rel in _ROOM_KEYS:
        p = profile if not rel else profile / rel
        try:
            if p.is_dir():
                rooms[key] = str(p)
        except OSError:
            continue
    work: dict[str, str] = {}
    for parts in _WORK_HINTS:
        p = profile.joinpath(*parts)
        try:
            if p.is_dir():
                work["/".join(parts)] = str(p)
        except OSError:
            continue

    doors = {name: _port_open(port) for name, port in _DOORS}

    host = "cmd" if os.name == "nt" else "posix"
    try:
        from remedy.execution.host.dialect import load_dialect

        host = load_dialect(home).host or host
    except Exception:
        pass

    disk_free = 0.0
    try:
        usage = shutil.disk_usage(profile)
        disk_free = round(usage.free / (1024**3), 1)
    except OSError:
        pass

    hostname = ""
    try:
        hostname = socket.gethostname()[:80]
    except OSError:
        hostname = ""

    return HomeCensus(
        stretched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        os_name=platform.system(),
        os_release=platform.release(),
        arch=platform.machine(),
        hostname=hostname,
        cpu_count=cpu,
        ram_total_mb=hw_ram_t,
        ram_avail_mb=hw_ram_a,
        gpu_name=hw_gpu,
        vram_total_mb=hw_vram,
        nvidia=nvidia,
        gpu_vendor=gpu_vendor,
        gpus=gpus,
        disk_home_free_gb=disk_free,
        tools=tools,
        missing=missing,
        rooms=rooms,
        work_rooms=work,
        doors=doors,
        host=host,
    )


def _port_open(port: int, *, timeout: float = 0.12) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _gb(mb: int) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.0f} GB" if mb % 1024 < 80 else f"{mb / 1024:.1f} GB"
    return f"{mb} MB"


def _safe_gpu_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")[:80]
        vendor = str(item.get("vendor") or "")[:16]
        if not name and not vendor:
            continue
        try:
            vram = int(item.get("vram_total_mb") or 0)
        except (TypeError, ValueError):
            vram = 0
        out.append(
            {
                "name": name,
                "vendor": vendor,
                "vram_total_mb": vram,
                "dedicated": bool(item.get("dedicated", True)),
                "backend": str(item.get("backend") or "")[:16],
            }
        )
    return out


def _safe_str_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = str(k)
        if _looks_secret(key) or _looks_secret(str(v)):
            continue
        out[key] = str(v)[:400]
        if len(out) >= 48:
            break
    return out


def _looks_secret(text: str) -> bool:
    low = (text or "").lower()
    return any(bit in low for bit in _SECRET_KEY_BITS)


def _home(home: str | Path | None) -> Path:
    if home:
        return Path(home).expanduser()
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir()
    except Exception:
        env = (os.environ.get("REMEDY_HOME") or "").strip()
        return Path(env or "~/.remedy").expanduser()
