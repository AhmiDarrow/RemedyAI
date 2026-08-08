"""Aggregate health diagnostics for Remedy API, RMB, hardware, and providers.

Used by GET /api/diagnostics — one snapshot for the desktop Health screen.
Keep collection cheap: no heavy model discovery or remote chat probes by default.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from remedy import __version__ as _remedy_version

logger = logging.getLogger(__name__)

_LOCAL_PROVIDER_IDS = frozenset(
    {"rmb", "ollama", "llamacpp", "demo", "kobold", "lmstudio", "local"}
)

# Short TTL cache for expensive OS probes (auto-refresh every ~8s in UI).
_hw_cache: dict[str, Any] = {"ts": 0.0, "payload": None}
_HW_CACHE_TTL_S = 12.0


def _gb(n: float | int | None) -> float | None:
    if n is None:
        return None
    try:
        return round(float(n) / (1024**3), 2)
    except (TypeError, ValueError):
        return None


def _mb(n: float | int | None) -> float | None:
    if n is None:
        return None
    try:
        return round(float(n) / (1024**2), 1)
    except (TypeError, ValueError):
        return None


def _human_uptime(seconds: float) -> str:
    s = max(0, int(seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _disk_for(path: Path) -> dict[str, Any]:
    try:
        p = path if path.exists() else path.parent
        usage = shutil.disk_usage(str(p))
        used_pct = round(100.0 * usage.used / usage.total, 1) if usage.total else None
        return {
            "path": str(path),
            "total_gb": _gb(usage.total),
            "used_gb": _gb(usage.used),
            "free_gb": _gb(usage.free),
            "used_pct": used_pct,
        }
    except Exception as exc:
        return {"path": str(path), "error": str(exc)[:200]}


def _process_stats() -> dict[str, Any]:
    out: dict[str, Any] = {"pid": os.getpid()}
    try:
        import psutil  # type: ignore

        p = psutil.Process(os.getpid())
        with contextlib.suppress(Exception):
            out["cpu_pct"] = round(float(p.cpu_percent(interval=0.05)), 1)
        with contextlib.suppress(Exception):
            mi = p.memory_info()
            out["rss_mb"] = _mb(mi.rss)
            out["vms_mb"] = _mb(getattr(mi, "vms", None))
        with contextlib.suppress(Exception):
            out["threads"] = int(p.num_threads())
        with contextlib.suppress(Exception):
            out["create_time"] = float(p.create_time())
        return out
    except Exception:
        pass
    # Windows fallback without psutil
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
            GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if GetProcessMemoryInfo(GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                out["rss_mb"] = _mb(counters.WorkingSetSize)
        except Exception:
            pass
    else:
        with contextlib.suppress(Exception):
            import resource

            # ru_maxrss is KB on Linux
            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            out["rss_mb"] = round(rss_kb / 1024.0, 1) if os.uname().sysname == "Linux" else _mb(rss_kb)
    return out


def _cpu_brand() -> str | None:
    try:
        if platform.system() == "Windows":
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Processor).Name",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            name = (r.stdout or "").strip().splitlines()
            if name:
                return name[0].strip()[:120]
    except Exception:
        pass
    with contextlib.suppress(Exception):
        return platform.processor() or None
    return None


def _hardware_stats() -> dict[str, Any]:
    now = time.time()
    cached = _hw_cache.get("payload")
    if (
        cached is not None
        and (now - float(_hw_cache.get("ts") or 0)) < _HW_CACHE_TTL_S
    ):
        return dict(cached)

    mem_total = None
    mem_avail = None
    cpu_pct = None
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        mem_total = vm.total
        mem_avail = vm.available
        cpu_pct = round(float(psutil.cpu_percent(interval=0.05)), 1)
    except Exception:
        if os.name == "nt":
            try:
                r = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "(Get-CimInstance Win32_OperatingSystem) | "
                        "Select-Object -ExpandProperty TotalVisibleMemorySize;"
                        "(Get-CimInstance Win32_OperatingSystem) | "
                        "Select-Object -ExpandProperty FreePhysicalMemory",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
                if len(lines) >= 2:
                    # values in KB
                    mem_total = int(float(lines[0])) * 1024
                    mem_avail = int(float(lines[1])) * 1024
            except Exception:
                pass

    used_pct = None
    if mem_total and mem_avail is not None and mem_total > 0:
        used_pct = round(100.0 * (mem_total - mem_avail) / mem_total, 1)

    gpu = _nvidia_gpu_stats()
    home = Path.home()
    disks = [
        _disk_for(home / ".remedy"),
        _disk_for(home),
    ]
    # de-dupe by root
    seen: set[str] = set()
    uniq_disks: list[dict[str, Any]] = []
    for d in disks:
        key = str(d.get("path") or "")
        root = Path(key).anchor or key
        if root in seen:
            continue
        seen.add(root)
        uniq_disks.append(d)

    payload = {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": socket.gethostname(),
        "cpu": {
            "count_logical": os.cpu_count(),
            "percent": cpu_pct,
            "brand": _cpu_brand(),
        },
        "memory": {
            "total_gb": _gb(mem_total),
            "available_gb": _gb(mem_avail),
            "used_pct": used_pct,
        },
        "gpu": gpu,
        "disks": uniq_disks,
    }
    _hw_cache["ts"] = now
    _hw_cache["payload"] = payload
    return dict(payload)


def _nvidia_gpu_stats() -> dict[str, Any]:
    out: dict[str, Any] = {"nvidia": False, "gpus": []}
    try:
        from remedy.vision.health import detect_nvidia

        out["nvidia"] = bool(detect_nvidia())
    except Exception:
        out["nvidia"] = False

    if not out["nvidia"]:
        return out

    try:
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=creation,
        )
        if r.returncode != 0:
            out["error"] = ((r.stderr or r.stdout) or "nvidia-smi failed")[:200]
            return out
        gpus: list[dict[str, Any]] = []
        for line in (r.stdout or "").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                gpus.append(
                    {
                        "name": parts[0][:80],
                        "memory_total_mb": float(parts[1]),
                        "memory_used_mb": float(parts[2]),
                        "util_pct": float(parts[3]),
                        "temp_c": float(parts[4]),
                    }
                )
            except (TypeError, ValueError):
                continue
        out["gpus"] = gpus
    except FileNotFoundError:
        out["error"] = "nvidia-smi not on PATH"
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _http_latency_ms(url: str, timeout: float = 1.5) -> float | None:
    """Cheap GET latency to a loopback OpenAI-compatible /models endpoint."""
    from urllib.request import Request

    try:
        from remedy.core.security import is_loopback_service_url, urlopen_no_redirect
    except Exception:
        return None
    base = (url or "").rstrip("/")
    if not base or not is_loopback_service_url(base):
        return None
    if base.endswith("/v1"):
        probe = base + "/models"
    elif base.endswith("/v1/models"):
        probe = base
    else:
        probe = base.rstrip("/") + "/v1/models"
    t0 = time.perf_counter()
    try:
        req = Request(probe, headers={"User-Agent": "RemedyAI-Diagnostics/1.0"})
        with urlopen_no_redirect(req, timeout=timeout) as resp:  # type: ignore[union-attr]
            _ = resp.read(64)
            if 200 <= getattr(resp, "status", 200) < 300:
                return round((time.perf_counter() - t0) * 1000.0, 1)
    except Exception:
        return None
    return None


def _remedy_section(
    *,
    runtime: Any,
    gateway: Any,
    memory: Any,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    from remedy.core.metrics import default_registry

    gw_stats = gateway.stats() if gateway else {"running": False}
    mem_count = 0
    summary_sessions = 0
    chat_sessions = 0
    if memory:
        try:
            db = memory._ensure_db()
            mem_count = int(db.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0])
            summary_sessions = int(
                db.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
            )
            chat_sessions = int(db.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0])
        except Exception:
            pass

    skills_count = 0
    if runtime and hasattr(runtime, "skills") and runtime.skills:
        with contextlib.suppress(Exception):
            skills_count = len(runtime.skills.skills)

    home = Path(str(cfg.get("home_dir") or Path.home() / ".remedy"))
    if not home.name or str(home) == str(Path.home()):
        home = Path.home() / ".remedy"

    proc = _process_stats()
    uptime_s = 0.0
    with contextlib.suppress(Exception):
        create = proc.get("create_time")
        if create:
            uptime_s = max(0.0, time.time() - float(create))

    snap: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        snap = default_registry.snapshot()

    api_host = "127.0.0.1"
    api_port = 7400
    with contextlib.suppress(Exception):
        api_port = int(os.environ.get("REMEDY_PORT") or cfg.get("api_port") or 7400)

    return {
        "version": _remedy_version,
        "uptime_seconds": round(uptime_s, 1) if uptime_s else None,
        "uptime": _human_uptime(uptime_s) if uptime_s else str(gw_stats.get("uptime") or "N/A"),
        "api": {
            "host": api_host,
            "port": api_port,
            "base_url": f"http://{api_host}:{api_port}",
            "listening": _port_open(api_host, api_port),
        },
        "process": proc,
        "gateway": gw_stats,
        "memory_entries": mem_count,
        "skills_count": skills_count,
        "sessions_count": summary_sessions,
        "chat_sessions_count": chat_sessions,
        "home_dir": str(home),
        "home_disk": _disk_for(home),
        "log_dir": str(home / "logs"),
        "metrics_counters": len(snap.get("counters") or []) if isinstance(snap, dict) else 0,
        "active_provider": str(
            getattr(runtime, "_llm_provider", None) or cfg.get("llm_provider") or ""
        ),
        "active_model": str(
            getattr(runtime, "_llm_model", None) or cfg.get("llm_model") or ""
        ),
        "health_checks": {},
    }


def _rmb_section(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        from remedy.runtime.rmb.service import get_rmb_status, is_running

        st = get_rmb_status(cfg)
        base = str(st.get("base_url") or "http://127.0.0.1:8787/v1")
        latency = None
        if st.get("running") or is_running(force=True, require_http=False):
            latency = _http_latency_ms(base)
        return {
            "ok": bool(st.get("ok", True)),
            "enabled": bool(st.get("enabled")),
            "running": bool(st.get("running")),
            "ready": bool(st.get("ready")),
            "starting": bool(st.get("starting")),
            "model_id": st.get("model_id"),
            "model_name": (st.get("model") or {}).get("name") if isinstance(st.get("model"), dict) else None,
            "model_path": st.get("model_path"),
            "model_present": bool(st.get("model_present")),
            "runtime_present": bool(st.get("runtime_present")),
            "ctx_size": st.get("ctx_size"),
            "n_gpu_layers": st.get("n_gpu_layers"),
            "profile": st.get("profile"),
            "host": st.get("host"),
            "port": st.get("port"),
            "base_url": base,
            "nvidia": bool(st.get("nvidia")),
            "latency_ms": latency,
            "not_ready_hint": st.get("not_ready_hint"),
            "vision_suspended": bool(st.get("vision_suspended")),
            "local_agent_mode": bool(st.get("local_agent_mode")),
        }
    except Exception as exc:
        logger.debug("diagnostics rmb failed", exc_info=True)
        return {"ok": False, "error": str(exc)[:300]}


def _vision_section(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        from remedy.vision.service import get_status as vision_status

        st = vision_status(cfg)
        if not isinstance(st, dict):
            return {"ok": False, "error": "no status"}
        return {
            "ok": bool(st.get("ok", True)),
            "enabled": st.get("enabled"),
            "running": st.get("running"),
            "ready": st.get("ready"),
            "model_id": st.get("model_id") or (st.get("model") or {}).get("id"),
            "port": st.get("port"),
            "base_url": st.get("base_url"),
            "suspended_for_rmb": st.get("suspended") or st.get("rmb_exclusive"),
            "not_ready_hint": st.get("not_ready_hint") or st.get("hint"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _computer_section(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        from remedy.core.computer.host_bridge import get_host_bridge

        home = None
        if isinstance(cfg, dict) and cfg.get("home_dir"):
            home = cfg.get("home_dir")
        b = get_host_bridge(home)
        return {
            "ok": True,
            "host_connected": bool(b.host_connected()),
            "pending_jobs": int(b.pending_count()) if hasattr(b, "pending_count") else None,
            "jobs_root": str(getattr(b, "root", "") or ""),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _providers_section(cfg: dict[str, Any]) -> dict[str, Any]:
    from remedy.interfaces.config import (
        PROVIDER_CATALOG,
        detect_ollama,
        get_provider_keys,
        public_provider_catalog,
        resolve_provider_api_key,
    )

    catalog = {p["id"]: p for p in public_provider_catalog(cfg)}
    keys = get_provider_keys(cfg)
    ollama = detect_ollama()
    xai_connected = False
    with contextlib.suppress(Exception):
        from remedy.interfaces.xai_auth import load_credentials

        home = cfg.get("home_dir") if isinstance(cfg, dict) else None
        home_path = Path(str(home)) if home else Path.home() / ".remedy"
        xai_connected = bool(load_credentials(home_path).connected)

    enabled_raw = cfg.get("enabled_providers")
    if isinstance(enabled_raw, str) and enabled_raw.strip():
        enabled_set = {x.strip().lower() for x in enabled_raw.split(",") if x.strip()}
    elif isinstance(enabled_raw, list) and enabled_raw:
        enabled_set = {str(x).strip().lower() for x in enabled_raw if str(x).strip()}
    else:
        enabled_set = None

    last_by = cfg.get("last_model_by_provider") or {}
    if not isinstance(last_by, dict):
        last_by = {}

    items: list[dict[str, Any]] = []
    for pid, meta in catalog.items():
        connected = False
        reason = "no_credentials"
        if pid == "demo":
            connected = True
            reason = "demo"
        elif pid == "ollama":
            connected = bool(ollama.get("available"))
            reason = "ollama_up" if connected else "ollama_down"
        elif pid == "xai" and xai_connected:
            connected = True
            reason = "oauth_or_key"
        elif keys.get(pid):
            connected = True
            reason = "api_key"
        if not connected and pid not in ("demo", "ollama"):
            with contextlib.suppress(Exception):
                resolved = str(resolve_provider_api_key(cfg, pid) or "").strip()
                if resolved and resolved not in ("local", "unused", "rmb"):
                    connected = True
                    reason = "resolved_key"
        if not connected and pid in ("rmb", "llamacpp"):
            # RMB connectivity reported separately; mark from config if active
            if str(cfg.get("llm_provider") or "").lower() == pid:
                connected = True
                reason = "active_local"
        is_local = pid in _LOCAL_PROVIDER_IDS or bool(meta.get("free_tier") == "local")
        enabled = True if enabled_set is None else (pid in enabled_set)
        if pid == "demo" and connected:
            enabled = True
        items.append(
            {
                "id": pid,
                "label": meta.get("label") or (PROVIDER_CATALOG.get(pid) or {}).get("label") or pid,
                "connected": connected,
                "enabled": enabled,
                "reason": reason,
                "local": is_local,
                "last_model": last_by.get(pid) or None,
                "base_url": meta.get("base_url"),
                "badge": meta.get("badge"),
            }
        )

    # Sort: connected non-local first, then connected local, then rest
    def _sort_key(it: dict[str, Any]) -> tuple:
        return (
            0 if it.get("connected") else 1,
            0 if not it.get("local") else 1,
            str(it.get("label") or it.get("id") or ""),
        )

    items.sort(key=_sort_key)

    usage_7d: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):
        from remedy.core.usage_ledger import summary as usage_summary

        summ = usage_summary(range_days=7.0)
        if isinstance(summ, dict):
            for row in summ.get("by_provider") or []:
                if not isinstance(row, dict):
                    continue
                usage_7d.append(
                    {
                        "provider": row.get("provider"),
                        "total_tokens": row.get("total_tokens"),
                        "estimated_cost_usd": row.get("estimated_cost_usd"),
                        "events": row.get("events"),
                    }
                )

    active = {
        "provider": str(cfg.get("llm_provider") or ""),
        "model": str(cfg.get("llm_model") or ""),
        "base_url": str(cfg.get("llm_base_url") or ""),
    }

    remote = [i for i in items if i.get("connected") and not i.get("local")]
    local = [i for i in items if i.get("connected") and i.get("local")]

    return {
        "active": active,
        "connected_count": sum(1 for i in items if i.get("connected")),
        "remote_connected_count": len(remote),
        "local_connected_count": len(local),
        "providers": items,
        "usage_7d": usage_7d,
        "ollama": ollama if isinstance(ollama, dict) else {},
    }


def _derive_issues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    rmb = payload.get("rmb") or {}
    if rmb.get("enabled") and not rmb.get("model_present"):
        issues.append(
            {
                "severity": "warn",
                "area": "rmb",
                "message": "RMB enabled but GGUF not found",
                "hint": rmb.get("not_ready_hint") or "Place a coding GGUF under ~/.remedy/rmb/models/",
            }
        )
    if rmb.get("enabled") and rmb.get("model_present") and not rmb.get("runtime_present"):
        issues.append(
            {
                "severity": "warn",
                "area": "rmb",
                "message": "llama-server binary missing for RMB",
                "hint": "Install Local vision runtime once, or set REMEDY_LLAMA_SERVER",
            }
        )
    if rmb.get("enabled") and rmb.get("model_present") and rmb.get("runtime_present") and not rmb.get("running"):
        issues.append(
            {
                "severity": "info",
                "area": "rmb",
                "message": "RMB host not running",
                "hint": "Start RMB from Settings → Local models",
            }
        )
    hw = payload.get("hardware") or {}
    mem = hw.get("memory") or {}
    if isinstance(mem.get("used_pct"), (int, float)) and float(mem["used_pct"]) >= 92:
        issues.append(
            {
                "severity": "warn",
                "area": "hardware",
                "message": f"System RAM high ({mem.get('used_pct')}% used)",
                "hint": "Close other apps or lower RMB context / GPU layers",
            }
        )
    for d in hw.get("disks") or []:
        if isinstance(d.get("used_pct"), (int, float)) and float(d["used_pct"]) >= 95:
            issues.append(
                {
                    "severity": "error",
                    "area": "hardware",
                    "message": f"Disk nearly full ({d.get('used_pct')}%) at {d.get('path')}",
                    "hint": "Free space under ~/.remedy or move models",
                }
            )
        elif isinstance(d.get("used_pct"), (int, float)) and float(d["used_pct"]) >= 90:
            issues.append(
                {
                    "severity": "warn",
                    "area": "hardware",
                    "message": f"Disk low on free space ({d.get('used_pct')}%)",
                    "hint": str(d.get("path") or ""),
                }
            )
    gpu = hw.get("gpu") or {}
    for g in gpu.get("gpus") or []:
        tot = float(g.get("memory_total_mb") or 0)
        used = float(g.get("memory_used_mb") or 0)
        if tot > 0 and (used / tot) >= 0.95:
            issues.append(
                {
                    "severity": "warn",
                    "area": "hardware",
                    "message": f"GPU VRAM nearly full on {g.get('name')}",
                    "hint": f"{used:.0f}/{tot:.0f} MB — stop vision or lower RMB layers",
                }
            )
    prov = payload.get("providers") or {}
    active = prov.get("active") or {}
    ap = str(active.get("provider") or "").lower()
    if ap and ap not in _LOCAL_PROVIDER_IDS:
        match = next(
            (p for p in (prov.get("providers") or []) if p.get("id") == ap),
            None,
        )
        if match and not match.get("connected"):
            issues.append(
                {
                    "severity": "error",
                    "area": "providers",
                    "message": f"Active provider {ap} has no credentials",
                    "hint": "Open Settings → Providers and add an API key",
                }
            )
    home_disk = (payload.get("remedy") or {}).get("home_disk") or {}
    if isinstance(home_disk.get("free_gb"), (int, float)) and float(home_disk["free_gb"]) < 1.0:
        issues.append(
            {
                "severity": "error",
                "area": "remedy",
                "message": "Less than 1 GB free on Remedy home disk",
                "hint": str(home_disk.get("path") or "~/.remedy"),
            }
        )
    return issues


def _overall_status(issues: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    if any(i.get("severity") == "error" for i in issues):
        return "error"
    if any(i.get("severity") == "warn" for i in issues):
        return "degraded"
    rmb = payload.get("rmb") or {}
    if rmb.get("error"):
        return "degraded"
    return "ok"


async def collect_diagnostics(
    *,
    runtime: Any = None,
    gateway: Any = None,
    memory: Any = None,
    probe_providers: bool = False,
) -> dict[str, Any]:
    """Build full diagnostics snapshot (async so route stays non-blocking)."""
    import asyncio

    from remedy.core.metrics import default_health
    from remedy.interfaces.api_support import load_config

    t0 = time.perf_counter()
    cfg = load_config() or {}
    if not isinstance(cfg, dict):
        cfg = {}

    # Heavy-ish sync work off the event loop
    def _build() -> dict[str, Any]:
        remedy = _remedy_section(runtime=runtime, gateway=gateway, memory=memory, cfg=cfg)
        rmb = _rmb_section(cfg)
        vision = _vision_section(cfg)
        hardware = _hardware_stats()
        providers = _providers_section(cfg)
        computer = _computer_section(cfg)
        return {
            "remedy": remedy,
            "rmb": rmb,
            "vision": vision,
            "hardware": hardware,
            "providers": providers,
            "computer": computer,
        }

    body = await asyncio.to_thread(_build)

    # Async health checks (registered checkers)
    with contextlib.suppress(Exception):
        health = await default_health.check()
        body["remedy"]["health_checks"] = health.get("checks") or {}
        if health.get("uptime_seconds") is not None:
            body["remedy"]["uptime_seconds"] = round(float(health["uptime_seconds"]), 1)
            body["remedy"]["uptime"] = _human_uptime(float(health["uptime_seconds"]))

    if probe_providers:
        # Optional lightweight loopback probes only (never burn remote API quota)
        probes: list[dict[str, Any]] = []
        for p in body.get("providers", {}).get("providers") or []:
            if not p.get("connected"):
                continue
            if not p.get("local"):
                continue
            base = str(p.get("base_url") or "")
            if not base:
                continue
            ms = await asyncio.to_thread(_http_latency_ms, base)
            probes.append(
                {
                    "id": p.get("id"),
                    "base_url": base,
                    "latency_ms": ms,
                    "ok": ms is not None,
                }
            )
        body["providers"]["probes"] = probes

    issues = _derive_issues(body)
    overall = _overall_status(issues, body)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    return {
        "ok": overall != "error",
        "overall": overall,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "collect_ms": elapsed_ms,
        "issues": issues,
        **body,
    }
