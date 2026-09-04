"""First-home stretch — thin binding over Zig ``remedy_core``.

Authority lives in ``native/zig/src/host_stretch.zig``. No Python twin.
Result lives in ``~/.remedy/host/home.json``. Background ensure stays here
(orchestration only).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STALE_DAYS = 14


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
        gpus_raw = data.get("gpus") or []
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
            gpus=[g for g in gpus_raw if isinstance(g, dict)][:8],
            disk_home_free_gb=float(data.get("disk_home_free_gb") or 0.0),
            tools=_safe_str_map(tools_raw),
            missing=[str(m) for m in missing_raw if str(m)][:40],
            rooms=_safe_str_map(rooms_raw),
            work_rooms=_safe_str_map(work_raw),
            doors={
                str(k): bool(v)
                for k, v in (doors_raw.items() if isinstance(doors_raw, dict) else [])
                if isinstance(k, str) and not _looks_secret(k)
            },
            host=str(data.get("host") or "cmd"),
        )


_SECRET_KEY_BITS = ("key", "token", "secret", "password", "auth", "cookie")


def _looks_secret(text: str) -> bool:
    low = (text or "").lower()
    return any(bit in low for bit in _SECRET_KEY_BITS)


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


def _home_str(home: str | Path | None) -> str:
    if home:
        return str(Path(home).expanduser())
    return ""


def census_path(home: str | Path | None = None) -> Path:
    base = Path(_home_str(home)) if home else None
    if base is None:
        try:
            from remedy.core.security import get_home_dir

            base = get_home_dir()
        except Exception:
            import os

            env = (os.environ.get("REMEDY_HOME") or "").strip()
            base = Path(env or "~/.remedy").expanduser()
    return base / "host" / "home.json"


def load_census(home: str | Path | None = None) -> HomeCensus | None:
    from remedy.core.computer import host_binding

    raw = host_binding.stretch_load(_home_str(home))
    if raw is None:
        return None
    return HomeCensus.from_dict(raw)


def save_census(census: HomeCensus, home: str | Path | None = None) -> Path:
    """Test/helper persist — production stretch goes through Zig stretch_home."""
    from remedy.core.atomic_json import write_json_atomic

    path = census_path(home)
    write_json_atomic(path, census.to_dict())
    return path


def needs_stretch(home: str | Path | None = None, *, stale_days: int = _STALE_DAYS) -> bool:
    from remedy.core.computer import host_binding

    return host_binding.stretch_needs(_home_str(home), stale_days=stale_days)


def stretch_home(home: str | Path | None = None, *, force: bool = False) -> HomeCensus:
    """Probe this PC and persist the census via Zig."""
    from remedy.core.computer import host_binding

    return HomeCensus.from_dict(
        host_binding.stretch_home(_home_str(home), force=force)
    )


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
    from remedy.core.computer import host_binding

    return host_binding.stretch_format_line(
        _home_str(home),
        census.to_dict() if census is not None else None,
    )


def format_home_whoami(
    census: HomeCensus | None = None,
    home: str | Path | None = None,
) -> str:
    """Longer block for /whoami and /stretch."""
    from remedy.core.computer import host_binding

    return host_binding.stretch_format_whoami(
        _home_str(home),
        census.to_dict() if census is not None else None,
    )


def _stretch_safe(home: str | Path | None, force: bool) -> HomeCensus | None:
    try:
        return stretch_home(home, force=force)
    except Exception:
        logger.exception("home stretch failed")
        return None
