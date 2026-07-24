"""Host health hints for visual decoder install/run (disk, RAM, runtime)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from remedy.vision.catalog import (
    DEFAULT_MODEL_ID,
    get_model_spec,
    get_runtime_spec,
    total_install_bytes,
)
from remedy.vision.config import vision_root


def _total_ram_bytes() -> int | None:
    """Best-effort physical RAM size without hard deps."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        except Exception:
            return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")  # type: ignore[attr-defined]
        size = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined]
        return int(pages) * int(size)
    except Exception:
        return None


def _disk_free_bytes(path: Path) -> int | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return int(shutil.disk_usage(path).free)
    except Exception:
        try:
            return int(shutil.disk_usage(path.anchor or path).free)
        except Exception:
            return None


def detect_nvidia() -> bool:
    """Cheap GPU presence check (does not require CUDA toolkit)."""
    if os.name == "nt":
        for name in (
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ):
            if Path(name).is_file():
                return True
        try:
            import ctypes

            return bool(ctypes.windll.LoadLibrary("nvml.dll"))  # type: ignore[attr-defined]
        except Exception:
            pass
    else:
        if Path("/usr/bin/nvidia-smi").is_file() or Path("/usr/local/bin/nvidia-smi").is_file():
            return True
    return False


def system_health(
    *,
    model_id: str | None = None,
    runtime_id: str | None = None,
    home_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return resource snapshot + human warnings for UI."""
    mid = model_id or DEFAULT_MODEL_ID
    try:
        spec = get_model_spec(mid)
        min_ram = int(spec.min_ram_gb)
        need_bytes = total_install_bytes(mid, runtime_id)
    except Exception:
        min_ram = 6
        need_bytes = 3 * 1024**3

    rid = (runtime_id or "win-cpu-x64").strip() or "win-cpu-x64"
    try:
        runtime = get_runtime_spec(rid)
        runtime_platform = runtime.platform
    except Exception:
        runtime_platform = rid

    root = vision_root(home_dir)
    free = _disk_free_bytes(root)
    ram = _total_ram_bytes()
    nvidia = detect_nvidia()
    is_cpu = "cpu" in runtime_platform.lower()

    warnings: list[str] = []
    if ram is not None:
        ram_gb = ram / (1024**3)
        if ram_gb + 0.05 < min_ram:
            warnings.append(
                f"This PC has ~{ram_gb:.1f} GB RAM; the decoder recommends ≥{min_ram} GB. "
                "Decode may be slow or fail under memory pressure."
            )
    if free is not None and free < need_bytes + 512 * 1024 * 1024:
        free_gb = free / (1024**3)
        need_gb = need_bytes / (1024**3)
        warnings.append(
            f"Low free disk (~{free_gb:.1f} GB free; install needs ~{need_gb:.1f} GB). "
            "Free space before installing."
        )
    if is_cpu:
        warnings.append(
            "CPU runtime selected — visual decode will be slower than a CUDA/GPU build. "
            "Fine for occasional screenshots; GPU is better for heavy use."
        )
        if nvidia:
            warnings.append(
                "NVIDIA GPU detected. You can reinstall with prefer_cuda=true for faster decode."
            )
    elif not is_cpu and not nvidia:
        warnings.append(
            "CUDA runtime selected but no NVIDIA GPU was detected. "
            "If start fails, reinstall with the CPU runtime."
        )

    return {
        "ram_bytes": ram,
        "ram_gb": round(ram / (1024**3), 2) if ram else None,
        "disk_free_bytes": free,
        "disk_free_gb": round(free / (1024**3), 2) if free else None,
        "install_need_bytes": need_bytes,
        "install_need_gb": round(need_bytes / (1024**3), 2),
        "min_ram_gb": min_ram,
        "nvidia_detected": nvidia,
        "runtime_id": rid,
        "runtime_platform": runtime_platform,
        "cpu_runtime": is_cpu,
        "warnings": warnings,
    }
