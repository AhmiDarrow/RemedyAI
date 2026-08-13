"""Vendor-agnostic GPU probe — NVIDIA, AMD, Intel, Apple, and the rest.

Remedy's home is the whole machine. nvidia-smi is one sensor, not the only one.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NVIDIA_SMI = (
    "nvidia-smi",
    r"C:\Windows\System32\nvidia-smi.exe",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    "/usr/bin/nvidia-smi",
    "/usr/local/bin/nvidia-smi",
)

_VEN_RE = re.compile(r"VEN_([0-9A-F]{4})", re.I)
_VEN_MAP = {
    "10DE": "nvidia",
    "1002": "amd",
    "1022": "amd",
    "8086": "intel",
    "106B": "apple",
}

_SKIP_NAMES = (
    "microsoft basic display",
    "remote desktop",
    "virtualbox",
    "vmware svga",
    "hyper-v",
    "citrix",
    "parsec",
)


@dataclass(frozen=True)
class GpuDevice:
    name: str
    vendor: str  # nvidia | amd | intel | apple | other
    vram_total_mb: int
    vram_free_mb: int = 0
    dedicated: bool = True
    source: str = ""
    backend: str = ""  # cuda | hip | vulkan | metal | ""

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GpuSnapshot:
    devices: list[GpuDevice] = field(default_factory=list)

    @property
    def primary(self) -> GpuDevice | None:
        if not self.devices:
            return None
        dedicated = [d for d in self.devices if d.dedicated]
        pool = dedicated or self.devices
        return max(pool, key=lambda d: (d.vram_total_mb, d.dedicated))

    def to_public(self) -> dict[str, Any]:
        prim = self.primary
        return {
            "devices": [d.to_public() for d in self.devices],
            "primary": prim.to_public() if prim else None,
        }


def classify_vendor(name: str, pnp: str = "") -> str:
    """Map a device name / PnP id to a vendor id."""
    m = _VEN_RE.search(pnp or "")
    if m:
        mapped = _VEN_MAP.get(m.group(1).upper())
        if mapped:
            return mapped
    low = f"{name} {pnp}".lower()
    if any(k in low for k in ("nvidia", "geforce", "rtx ", "gtx ", "quadro", "tesla", "titan")):
        return "nvidia"
    if any(k in low for k in ("amd", "radeon", "instinct", "firepro", "rx ", "vega")):
        return "amd"
    if any(k in low for k in ("intel", "arc ", "iris", "uhd graphics", "hd graphics", "xe graphics")):
        return "intel"
    if any(k in low for k in ("apple", "m1 ", "m2 ", "m3 ", "m4 ", "metal")):
        return "apple"
    return "other"


def looks_like_igpu(name: str, vendor: str) -> bool:
    """True for typical integrated parts (still recorded; not the autofit card)."""
    low = (name or "").lower()
    if vendor == "intel" and "arc" not in low:
        return True
    if vendor == "amd" and not any(
        k in low for k in ("rx ", "xt", "pro ", "instinct", "vii", "fury", "7900", "7800", "7700", "7600")
    ):
        if "radeon graphics" in low or "radeon(tm) graphics" in low or low.strip() == "graphics":
            return True
        if re.search(r"radeon\s+\d{3,4}m?\b", low) and "rx" not in low:
            return True
    return False


def classify_backend(vendor: str) -> str:
    if vendor == "nvidia":
        return "cuda"
    if vendor == "amd":
        return "hip" if _which_any(("rocm-smi", "amd-smi")) else "vulkan"
    if vendor == "intel":
        return "vulkan"
    if vendor == "apple":
        return "metal"
    return ""


def probe_gpus() -> GpuSnapshot:
    """Collect GPUs from every cheap sensor. First success per vendor is enough."""
    found: list[GpuDevice] = []
    seen: set[str] = set()

    def _add(dev: GpuDevice) -> None:
        key = f"{dev.vendor}:{dev.name.lower()}"
        if key in seen:
            return
        if _skip_name(dev.name):
            return
        seen.add(key)
        found.append(dev)

    for d in _probe_nvidia_smi():
        _add(d)
    for d in _probe_amd_smi():
        _add(d)
    for d in _probe_windows_cim():
        _add(d)
    for d in _probe_linux_sysfs():
        _add(d)
    return GpuSnapshot(devices=found)


def probe_primary_vram() -> tuple[bool, int, int, str, str]:
    """Compat tuple: (is_nvidia, total_mb, free_mb, name, vendor)."""
    snap = probe_gpus()
    prim = snap.primary
    if prim is None:
        return False, 0, 0, "", ""
    return (
        prim.vendor == "nvidia",
        int(prim.vram_total_mb or 0),
        int(prim.vram_free_mb or 0),
        prim.name,
        prim.vendor,
    )


def _skip_name(name: str) -> bool:
    low = (name or "").lower()
    return any(s in low for s in _SKIP_NAMES) or not low.strip()


def _creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)


def _run(argv: list[str], *, timeout: float = 4.0) -> str:
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_creationflags(),
        )
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout or ""


def _which_any(names: tuple[str, ...]) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
        if os.name == "nt" and not n.lower().endswith(".exe"):
            p = shutil.which(n + ".exe")
            if p:
                return p
    return None


def _find_nvidia_smi() -> str | None:
    for cand in _NVIDIA_SMI:
        p = Path(cand)
        if p.is_file():
            return str(p)
    return shutil.which("nvidia-smi")


def _probe_nvidia_smi() -> list[GpuDevice]:
    smi = _find_nvidia_smi()
    if not smi:
        return []
    out = _run(
        [
            smi,
            "--query-gpu=memory.total,memory.free,name",
            "--format=csv,noheader,nounits",
        ]
    )
    devices: list[GpuDevice] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            total = int(float(parts[0]))
            free = int(float(parts[1]))
        except (TypeError, ValueError):
            continue
        name = parts[2] if len(parts) > 2 else "NVIDIA GPU"
        if total <= 0:
            continue
        devices.append(
            GpuDevice(
                name=name[:80],
                vendor="nvidia",
                vram_total_mb=total,
                vram_free_mb=free,
                dedicated=True,
                source="nvidia-smi",
                backend="cuda",
            )
        )
    if devices:
        return devices
    listed = _run([smi, "-L"], timeout=3.0)
    if listed.strip():
        name = listed.strip().splitlines()[0][:80]
        return [
            GpuDevice(
                name=name or "NVIDIA GPU",
                vendor="nvidia",
                vram_total_mb=8192,
                vram_free_mb=6144,
                dedicated=True,
                source="nvidia-smi-L",
                backend="cuda",
            )
        ]
    return []


def _probe_amd_smi() -> list[GpuDevice]:
    exe = _which_any(("amd-smi", "rocm-smi"))
    if not exe:
        return []
    devices: list[GpuDevice] = []
    # amd-smi static --json (newer)
    if Path(exe).name.lower().startswith("amd-smi"):
        raw = _run([exe, "static", "--json"], timeout=6.0)
        devices.extend(_parse_amd_smi_json(raw))
        if devices:
            return devices
    # rocm-smi --showmeminfo vram
    raw = _run([exe, "--showmeminfo", "vram"], timeout=5.0)
    devices.extend(_parse_rocm_smi(raw))
    if devices:
        return devices
    # Presence-only
    raw = _run([exe, "-i"], timeout=3.0) or _run([exe, "--showproductname"], timeout=3.0)
    if raw.strip():
        name = "AMD GPU"
        for line in raw.splitlines():
            if "card" in line.lower() or "gpu" in line.lower() or "radeon" in line.lower():
                name = line.strip()[:80]
                break
        return [
            GpuDevice(
                name=name,
                vendor="amd",
                vram_total_mb=8192,
                vram_free_mb=6144,
                dedicated=True,
                source="rocm-smi",
                backend="hip",
            )
        ]
    return []


def _parse_amd_smi_json(raw: str) -> list[GpuDevice]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    out: list[GpuDevice] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Nested shapes vary by amd-smi version
        name = str(
            item.get("market_name")
            or item.get("product_name")
            or (item.get("board") or {}).get("product_name")
            or "AMD GPU"
        )[:80]
        vram = 0
        mem = item.get("vram") or item.get("memory") or {}
        if isinstance(mem, dict):
            for key in ("size", "vram_total", "total"):
                vram = _mb_from_unknown(mem.get(key))
                if vram:
                    break
        out.append(
            GpuDevice(
                name=name,
                vendor="amd",
                vram_total_mb=vram or 8192,
                vram_free_mb=max(0, (vram or 8192) - 1024),
                dedicated=True,
                source="amd-smi",
                backend="hip",
            )
        )
    return out


def _parse_rocm_smi(raw: str) -> list[GpuDevice]:
    out: list[GpuDevice] = []
    total = 0
    used = 0
    for line in raw.splitlines():
        low = line.lower()
        nums = re.findall(r"(\d+)\s*(miB|mb|giB|gb)?", line, re.I)
        if "total" in low and nums:
            total = _mb_from_unknown(nums[0][0], nums[0][1])
        if ("used" in low or "busy" in low) and nums:
            used = _mb_from_unknown(nums[0][0], nums[0][1])
    if total > 0:
        out.append(
            GpuDevice(
                name="AMD GPU",
                vendor="amd",
                vram_total_mb=total,
                vram_free_mb=max(0, total - used),
                dedicated=True,
                source="rocm-smi",
                backend="hip",
            )
        )
    return out


def _probe_windows_cim() -> list[GpuDevice]:
    if os.name != "nt":
        return []
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return _probe_windows_wmic()
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM,PNPDeviceID | ConvertTo-Json -Compress"
    )
    raw = _run(
        [ps, "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=6.0,
    )
    devices = _parse_cim_json(raw)
    return devices or _probe_windows_wmic()


def _parse_cim_json(raw: str) -> list[GpuDevice]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    out: list[GpuDevice] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        pnp = str(item.get("PNPDeviceID") or "")
        if _skip_name(name):
            continue
        vendor = classify_vendor(name, pnp)
        ram = _mb_from_unknown(item.get("AdapterRAM"))
        # AdapterRAM is often a 32-bit overflow above 4 GB — keep as lower bound
        if ram >= 4090:
            ram = max(ram, 4096)
        dedicated = not looks_like_igpu(name, vendor)
        if ram <= 0 and dedicated:
            ram = 4096
        if ram <= 0:
            continue
        out.append(
            GpuDevice(
                name=name[:80],
                vendor=vendor,
                vram_total_mb=int(ram),
                vram_free_mb=max(0, int(ram) - 512),
                dedicated=dedicated,
                source="wmi",
                backend=classify_backend(vendor),
            )
        )
    return out


def _probe_windows_wmic() -> list[GpuDevice]:
    wmic = shutil.which("wmic")
    if not wmic:
        return []
    raw = _run(
        [wmic, "path", "win32_videocontroller", "get", "Name,AdapterRAM,PNPDeviceID", "/format:csv"],
        timeout=6.0,
    )
    out: list[GpuDevice] = []
    for line in raw.splitlines():
        if not line.strip() or line.lower().startswith("node"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        # CSV: Node,AdapterRAM,Name,PNPDeviceID  (column order can vary)
        name = ""
        pnp = ""
        ram_s = ""
        for p in parts[1:]:
            if p.upper().startswith("PCI\\") or "VEN_" in p.upper():
                pnp = p
            elif p.isdigit():
                ram_s = p
            elif p and not name:
                name = p
        if _skip_name(name):
            continue
        vendor = classify_vendor(name, pnp)
        ram = _mb_from_unknown(ram_s)
        dedicated = not looks_like_igpu(name, vendor)
        if ram <= 0 and dedicated:
            ram = 4096
        if ram <= 0:
            continue
        out.append(
            GpuDevice(
                name=name[:80],
                vendor=vendor,
                vram_total_mb=int(ram),
                vram_free_mb=max(0, int(ram) - 512),
                dedicated=dedicated,
                source="wmic",
                backend=classify_backend(vendor),
            )
        )
    return out


def _probe_linux_sysfs() -> list[GpuDevice]:
    drm = Path("/sys/class/drm")
    if not drm.is_dir():
        return []
    out: list[GpuDevice] = []
    for card in sorted(drm.glob("card[0-9]")):
        dev = card / "device"
        if not dev.is_dir():
            continue
        vendor = _sysfs_vendor(dev / "vendor")
        name = _sysfs_name(dev) or card.name
        if _skip_name(name) and vendor == "other":
            continue
        vram = _sysfs_vram_mb(dev)
        dedicated = not looks_like_igpu(name, vendor)
        if vram <= 0 and dedicated:
            vram = 4096
        if vram <= 0:
            continue
        out.append(
            GpuDevice(
                name=name[:80],
                vendor=vendor,
                vram_total_mb=vram,
                vram_free_mb=max(0, vram - 512),
                dedicated=dedicated,
                source="sysfs",
                backend=classify_backend(vendor),
            )
        )
    return out


def _sysfs_vendor(path: Path) -> str:
    try:
        raw = path.read_text(encoding="ascii", errors="ignore").strip().lower()
    except OSError:
        return "other"
    return {
        "0x10de": "nvidia",
        "0x1002": "amd",
        "0x1022": "amd",
        "0x8086": "intel",
        "0x106b": "apple",
    }.get(raw, "other")


def _sysfs_name(dev: Path) -> str:
    for rel in ("product_name", "label"):
        try:
            t = (dev / rel).read_text(encoding="utf-8", errors="ignore").strip()
            if t:
                return t
        except OSError:
            continue
    try:
        uevent = (dev / "uevent").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in uevent.splitlines():
        if line.startswith("DRIVER="):
            return line.split("=", 1)[-1].strip()
    return ""


def _sysfs_vram_mb(dev: Path) -> int:
    for rel in ("mem_info_vram_total", "mem_info_vis_vram_total"):
        try:
            raw = (dev / rel).read_text(encoding="ascii", errors="ignore").strip()
            n = int(raw)
            if n > 1_000_000:
                return n // (1024 * 1024)
            if n > 0:
                return n
        except (OSError, ValueError):
            continue
    return 0


def _mb_from_unknown(value: Any, unit: str = "") -> int:
    if value is None or value == "":
        return 0
    try:
        n = float(value)
    except (TypeError, ValueError):
        m = re.search(r"([\d.]+)", str(value))
        if not m:
            return 0
        n = float(m.group(1))
        unit = unit or str(value)
    u = (unit or "").lower()
    if "gi" in u or (u.endswith("gb") and n < 256):
        return int(n * 1024)
    if n > 8_000_000:  # bytes
        return int(n // (1024 * 1024))
    return int(n)


def runtime_matches_gpu(
    vendor: str,
    *,
    runtime_id: str = "",
    binary: str | Path | None = None,
) -> bool:
    """True when this llama/vision runtime can actually use *vendor*."""
    rid = (runtime_id or "").lower()
    bin_s = str(binary or "").lower()
    blob = f"{rid} {bin_s}"
    if "cpu" in rid and "cuda" not in rid and "vulkan" not in rid and "hip" not in rid:
        return False
    if vendor == "nvidia":
        return "cpu" not in rid or "cuda" in blob or "nvidia" in blob
    if vendor == "amd":
        return any(k in blob for k in ("vulkan", "hip", "rocm", "amd"))
    if vendor == "intel":
        return "vulkan" in blob or "sycl" in blob or "intel" in blob
    if vendor == "apple":
        return "metal" in blob
    return "vulkan" in blob
