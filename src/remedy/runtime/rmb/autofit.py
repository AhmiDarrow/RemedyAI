"""Hardware-aware RMB fit — pick ctx / GPU layers / KV cache that actually load.

Default product path: the user drops a GGUF, Remedy measures VRAM/RAM and
starts the largest *stable* agent window that fits. Manual ctx / ngl still
win when the user locks them.

This module is pure planning (no process spawn). ``service.start_rmb_server``
applies the plan and may walk it down on OOM.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Agent work wants a long rolling window. 32k is the default ceiling — bigger
# is allowed when the user locks it, but we never *plan* past this.
_MAX_PLAN_CTX = 32_768
_MIN_PLAN_CTX = 4_096
_CTX_LADDER = (32_768, 24_576, 16_384, 12_288, 8_192, 6_144, 4_096)

# Leave headroom for CUDA context, fragmentation, and decode scratch.
_VRAM_USE_FRAC = 0.82
_CUDA_OVERHEAD_MB = 900
_CPU_OVERHEAD_MB = 256

_SIZE_RE = re.compile(
    r"(?:^|[^0-9])(0?\.5|1\.5|[0-9]+(?:\.[0-9]+)?)\s*b(?:[^0-9]|$)",
    re.IGNORECASE,
)

# Filename → (n_layer, n_kv_head, head_dim) when GGUF metadata is missing.
_ARCH_BY_SIZE: dict[str, tuple[int, int, int]] = {
    "0.5b": (24, 2, 64),
    "1b": (22, 4, 64),
    "1.5b": (28, 2, 64),
    "3b": (36, 2, 128),
    "4b": (36, 4, 128),
    "7b": (28, 4, 128),
    "8b": (32, 8, 128),
    "9b": (36, 8, 128),
    "13b": (40, 8, 128),
    "14b": (48, 8, 128),
    "27b": (64, 8, 128),
    "32b": (64, 8, 128),
    "35b": (64, 8, 128),
    "70b": (80, 8, 128),
    "72b": (80, 8, 128),
}

# GGUF value types (ggml)
_GGUF_UINT8, _GGUF_INT8 = 0, 1
_GGUF_UINT16, _GGUF_INT16 = 2, 3
_GGUF_UINT32, _GGUF_INT32 = 4, 5
_GGUF_FLOAT32, _GGUF_BOOL = 6, 7
_GGUF_STRING, _GGUF_ARRAY = 8, 9
_GGUF_UINT64, _GGUF_INT64, _GGUF_FLOAT64 = 10, 11, 12

_OOM_MARKERS = (
    "out of memory",
    "outofmemory",
    "cuda error",
    "cuda_error",
    "failed to allocate",
    "ggml_gallocr",
    "ggml_backend_cuda",
    "insufficient memory",
    "cuda malloc",
    "hip malloc",
    "vk::outofdevicememory",
    "oom",
)
_FLASH_MARKERS = (
    "flash attn",
    "flash_attn",
    "flash-attn",
    "failed to init flash",
    "fa_type",
)
_UNKNOWN_FLAG_MARKERS = (
    "unknown argument",
    "unrecognized option",
    "invalid argument",
    "error: unknown",
)


@dataclass(frozen=True)
class HardwareProbe:
    nvidia: bool
    vram_total_mb: int
    vram_free_mb: int
    gpu_name: str
    ram_total_mb: int
    ram_avail_mb: int
    cpu_count: int
    gpu_vendor: str = ""
    gpu_backend: str = ""

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        d["has_gpu"] = self.has_gpu
        d["usable_gpu"] = self.usable_gpu
        return d

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpu_name or self.vram_total_mb >= 512 or self.gpu_vendor)

    @property
    def usable_gpu(self) -> bool:
        """Dedicated-enough VRAM for llama.cpp offload, any vendor."""
        if self.vram_total_mb < 2048:
            return False
        return bool(self.gpu_vendor or self.nvidia or self.gpu_name)


@dataclass(frozen=True)
class ModelArch:
    size_label: str
    n_params_b: float
    n_layer: int
    n_kv_head: int
    head_dim: int
    weight_bytes: int
    family: str
    source: str
    train_ctx: int = 0

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AutofitPlan:
    ctx_size: int
    n_gpu_layers: int
    cache_type: str
    flash_attn: bool
    batch_size: int
    ubatch_size: int
    threads: int
    cache_reuse: int
    parallel: int
    target: str
    vram_budget_mb: int
    kv_mb: int
    weight_mb: int
    estimated_used_mb: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    hardware: dict[str, Any] = field(default_factory=dict)
    arch: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        d["summary"] = self.summary()
        return d

    def summary(self) -> str:
        ngl = "all" if self.n_gpu_layers < 0 else str(self.n_gpu_layers)
        kv = self.cache_type or "f16"
        where = "GPU" if self.n_gpu_layers != 0 else "CPU"
        return f"{self.ctx_size} tok · {ngl} layers · {kv} KV · {where}"


def _find_nvidia_smi() -> str | None:
    from remedy.runtime.gpu_probe import _find_nvidia_smi as _find

    return _find()


def probe_vram() -> tuple[bool, int, int, str]:
    """Return (is_nvidia, total_mb, free_mb, name). Any vendor may fill VRAM/name."""
    nvidia, total, free, name, _vendor = _probe_vram_full()
    return nvidia, total, free, name


def _probe_vram_full() -> tuple[bool, int, int, str, str]:
    try:
        from remedy.runtime.gpu_probe import probe_primary_vram

        return probe_primary_vram()
    except Exception:
        logger.debug("gpu probe failed", exc_info=True)
        return False, 0, 0, "", ""


def probe_ram() -> tuple[int, int]:
    """Return (total_mb, avail_mb). Zeros when unknown."""
    if sys.platform == "win32":
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
                return (
                    int(stat.ullTotalPhys // (1024 * 1024)),
                    int(stat.ullAvailPhys // (1024 * 1024)),
                )
        except Exception:
            logger.debug("GlobalMemoryStatusEx failed", exc_info=True)
    else:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            avail = os.sysconf("SC_AVPHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * size // (1024 * 1024)), int(avail * size // (1024 * 1024))
        except Exception:
            return 0, 0
    return 0, 0


def probe_hardware() -> HardwareProbe:
    nvidia, vram_t, vram_f, name, vendor = _probe_vram_full()
    ram_t, ram_a = probe_ram()
    cpus = os.cpu_count() or 4
    backend = ""
    try:
        from remedy.runtime.gpu_probe import classify_backend

        backend = classify_backend(vendor) if vendor else ""
    except Exception:
        backend = "cuda" if nvidia else ""
    return HardwareProbe(
        nvidia=nvidia,
        vram_total_mb=max(0, int(vram_t)),
        vram_free_mb=max(0, int(vram_f)),
        gpu_name=name or "",
        ram_total_mb=max(0, int(ram_t)),
        ram_avail_mb=max(0, int(ram_a)),
        cpu_count=max(1, int(cpus)),
        gpu_vendor=vendor or ("nvidia" if nvidia else ""),
        gpu_backend=backend,
    )


def _read_gguf_string(f) -> str:
    (n,) = struct.unpack("<Q", f.read(8))
    if n > 1_000_000:
        raise ValueError("gguf string too long")
    raw = f.read(int(n))
    return raw.decode("utf-8", errors="replace")


def _skip_gguf_value(f, vtype: int) -> None:
    if vtype in (_GGUF_UINT8, _GGUF_INT8, _GGUF_BOOL):
        f.read(1)
    elif vtype in (_GGUF_UINT16, _GGUF_INT16):
        f.read(2)
    elif vtype in (_GGUF_UINT32, _GGUF_INT32, _GGUF_FLOAT32):
        f.read(4)
    elif vtype in (_GGUF_UINT64, _GGUF_INT64, _GGUF_FLOAT64):
        f.read(8)
    elif vtype == _GGUF_STRING:
        _read_gguf_string(f)
    elif vtype == _GGUF_ARRAY:
        (atype,) = struct.unpack("<I", f.read(4))
        (count,) = struct.unpack("<Q", f.read(8))
        if count > 2_000_000:
            raise ValueError("gguf array too large")
        for _ in range(int(count)):
            _skip_gguf_value(f, int(atype))
    else:
        raise ValueError(f"unknown gguf type {vtype}")


def _read_gguf_scalar(f, vtype: int) -> Any:
    if vtype == _GGUF_UINT8:
        return struct.unpack("<B", f.read(1))[0]
    if vtype == _GGUF_INT8:
        return struct.unpack("<b", f.read(1))[0]
    if vtype == _GGUF_UINT16:
        return struct.unpack("<H", f.read(2))[0]
    if vtype == _GGUF_INT16:
        return struct.unpack("<h", f.read(2))[0]
    if vtype == _GGUF_UINT32:
        return struct.unpack("<I", f.read(4))[0]
    if vtype == _GGUF_INT32:
        return struct.unpack("<i", f.read(4))[0]
    if vtype == _GGUF_FLOAT32:
        return struct.unpack("<f", f.read(4))[0]
    if vtype == _GGUF_BOOL:
        return bool(struct.unpack("<B", f.read(1))[0])
    if vtype == _GGUF_UINT64:
        return struct.unpack("<Q", f.read(8))[0]
    if vtype == _GGUF_INT64:
        return struct.unpack("<q", f.read(8))[0]
    if vtype == _GGUF_FLOAT64:
        return struct.unpack("<d", f.read(8))[0]
    if vtype == _GGUF_STRING:
        return _read_gguf_string(f)
    _skip_gguf_value(f, vtype)
    return None


_WANTED_META_SUFFIXES = (
    "block_count",
    "attention.head_count_kv",
    "attention.head_count",
    "embedding_length",
    "context_length",
)


def read_gguf_arch_metadata(path: Path | str | None) -> dict[str, Any]:
    """Read a handful of GGUF KV fields. Empty dict on any failure."""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    out: dict[str, Any] = {}
    try:
        with p.open("rb") as f:
            if f.read(4) != b"GGUF":
                return {}
            (version,) = struct.unpack("<I", f.read(4))
            if version < 1 or version > 4:
                return {}
            f.read(8)  # tensor_count
            (kv_count,) = struct.unpack("<Q", f.read(8))
            if kv_count > 10_000:
                return {}
            for _ in range(int(kv_count)):
                key = _read_gguf_string(f)
                (vtype,) = struct.unpack("<I", f.read(4))
                want = key == "general.architecture" or any(
                    key.endswith(sfx) for sfx in _WANTED_META_SUFFIXES
                )
                if want and vtype != _GGUF_ARRAY:
                    out[key] = _read_gguf_scalar(f, int(vtype))
                else:
                    _skip_gguf_value(f, int(vtype))
                if len(out) >= 8 and "general.architecture" in out:
                    # Have the useful set — stop early if we already collected them
                    if any(k.endswith("block_count") for k in out):
                        break
    except Exception:
        logger.debug("GGUF metadata parse failed for %s", path, exc_info=True)
        return {}
    return out


def _size_label_from_name(name: str) -> str:
    m = _SIZE_RE.search(name.lower().replace("_", "-"))
    if not m:
        return ""
    raw = m.group(1)
    try:
        n = float(raw)
    except ValueError:
        return raw + "b"
    if n == int(n):
        return f"{int(n)}b"
    return f"{n}b"


def _params_from_label(label: str) -> float:
    try:
        return float(label.replace("b", "").strip() or 0)
    except ValueError:
        return 0.0


def estimate_model_arch(model: Path | str | None) -> ModelArch:
    """Best-effort architecture from GGUF metadata, then filename + size."""
    p = Path(model) if model else Path("unknown.gguf")
    weight = 0
    with_file = p.is_file()
    if with_file:
        try:
            weight = int(p.stat().st_size)
        except OSError:
            weight = 0
    name = f"{p.name} {p.stem}".lower()
    label = _size_label_from_name(name)
    family = "unknown"
    for token in ("qwen3", "qwen2", "qwen", "llama", "mistral", "gemma", "phi", "deepseek"):
        if token in name:
            family = token
            break
    n_layer, n_kv, head_dim = _ARCH_BY_SIZE.get(label, (32, 8, 128))
    train_ctx = 0
    source = "filename"

    meta = read_gguf_arch_metadata(p) if with_file else {}
    if meta:
        arch = str(meta.get("general.architecture") or family or "llama")
        family = arch or family
        def _pick(*suffixes: str) -> Any:
            for sfx in suffixes:
                for k, v in meta.items():
                    if k == sfx or k.endswith("." + sfx) or k.endswith(sfx):
                        return v
            return None

        bc = _pick("block_count")
        kv = _pick("attention.head_count_kv")
        hd = _pick("attention.head_count")
        emb = _pick("embedding_length")
        tctx = _pick("context_length")
        try:
            if bc is not None:
                n_layer = max(1, int(bc))
                source = "gguf"
            if kv is not None:
                n_kv = max(1, int(kv))
                source = "gguf"
            if emb is not None and hd is not None and int(hd) > 0:
                head_dim = max(32, int(int(emb) // int(hd)))
                source = "gguf"
            if tctx is not None:
                train_ctx = max(0, int(tctx))
        except (TypeError, ValueError):
            pass

    if not label and weight:
        # Rough param guess from Q4 file size (~0.65 bytes/param)
        gb = weight / (1024**3)
        if gb < 1.2:
            label = "1b"
        elif gb < 2.5:
            label = "3b"
        elif gb < 6.5:
            label = "7b"
        elif gb < 11:
            label = "14b"
        elif gb < 22:
            label = "32b"
        else:
            label = "70b"
        if source != "gguf":
            n_layer, n_kv, head_dim = _ARCH_BY_SIZE.get(label, (n_layer, n_kv, head_dim))

    return ModelArch(
        size_label=label or "7b",
        n_params_b=_params_from_label(label or "7b"),
        n_layer=max(1, int(n_layer)),
        n_kv_head=max(1, int(n_kv)),
        head_dim=max(32, int(head_dim)),
        weight_bytes=max(0, int(weight)),
        family=family,
        source=source,
        train_ctx=int(train_ctx or 0),
    )


def kv_bytes_per_token(arch: ModelArch, cache_type: str = "") -> int:
    """KV cache bytes per token (K+V, all layers)."""
    ct = (cache_type or "").strip().lower()
    if ct in ("q4_0", "q4_1", "q4_k"):
        elem = 0.5
    elif ct in ("q8_0", "q8_1"):
        elem = 1.0
    else:
        elem = 2.0  # f16 / bf16
    return int(2 * arch.n_layer * arch.n_kv_head * arch.head_dim * elem)


def estimate_kv_mb(ctx: int, arch: ModelArch, cache_type: str = "", parallel: int = 1) -> int:
    bpt = kv_bytes_per_token(arch, cache_type)
    return max(1, int(ctx) * bpt * max(1, int(parallel)) // (1024 * 1024))


def _batch_for_leftover(leftover_mb: int, *, cpu: bool) -> tuple[int, int]:
    if cpu:
        return 512, 128
    if leftover_mb >= 3072:
        return 2048, 512
    if leftover_mb >= 1536:
        return 1024, 256
    return 512, 128


def _cpu_threads(hw: HardwareProbe) -> int:
    return max(1, min(16, int(hw.cpu_count) - 2))


def _fits(used_mb: int, budget_mb: int) -> bool:
    return used_mb > 0 and used_mb <= budget_mb


def _flash_ok(hw: HardwareProbe) -> bool:
    """Flash-attn is reliable on CUDA; other backends walk it down on start fail."""
    return bool(hw.nvidia or hw.gpu_vendor == "nvidia" or hw.gpu_backend == "cuda")


def plan_autofit(
    model: Path | str | None,
    *,
    hardware: HardwareProbe | None = None,
    last_good: dict[str, Any] | None = None,
    prefer_ctx: int | None = None,
    cache_reuse: int = 256,
) -> AutofitPlan:
    """Pick the largest stable agent window that fits this machine + GGUF."""
    hw = hardware or probe_hardware()
    arch = estimate_model_arch(model)
    weight_mb = max(1, arch.weight_bytes // (1024 * 1024)) if arch.weight_bytes else 4500
    reasons: list[str] = [f"arch:{arch.source}:{arch.size_label}:{arch.n_layer}L"]

    # Seed from a prior successful start of the *same* GGUF on similar VRAM.
    seeded = _plan_from_last_good(last_good, model, hw, arch, cache_reuse)
    if seeded is not None:
        return seeded

    cap = _MAX_PLAN_CTX
    if prefer_ctx and prefer_ctx >= _MIN_PLAN_CTX:
        cap = min(_MAX_PLAN_CTX, int(prefer_ctx))
    if arch.train_ctx and 2048 <= arch.train_ctx < cap:
        # Don't plan past the model's native train window
        cap = int(arch.train_ctx)
        reasons.append(f"train_ctx:{arch.train_ctx}")
    ladder = tuple(c for c in _CTX_LADDER if c <= cap) or (_MIN_PLAN_CTX,)

    if hw.usable_gpu:
        plan = _plan_gpu(hw, arch, weight_mb, ladder, cache_reuse, reasons)
        if plan is not None:
            return plan
        reasons.append("gpu_plan_missed")

    return _plan_cpu(hw, arch, weight_mb, ladder, cache_reuse, reasons)


def _plan_gpu(
    hw: HardwareProbe,
    arch: ModelArch,
    weight_mb: int,
    ladder: tuple[int, ...],
    cache_reuse: int,
    reasons: list[str],
) -> AutofitPlan | None:
    budget = int(hw.vram_total_mb * _VRAM_USE_FRAC)
    overhead = _CUDA_OVERHEAD_MB
    # If weights themselves overflow, fall through to partial / CPU
    if weight_mb + overhead + 256 > budget:
        return _plan_partial_gpu(hw, arch, weight_mb, ladder, cache_reuse, reasons)

    # Prefer full offload + largest ctx. Try f16 KV first (quality), then q8.
    for cache_type in ("", "q8_0"):
        for ctx in ladder:
            kv = estimate_kv_mb(ctx, arch, cache_type, 1)
            # Scratch scales a bit with ctx; keep it modest
            scratch = 768 if ctx >= 16384 else 512
            used = weight_mb + kv + overhead + scratch
            if not _fits(used, budget):
                continue
            leftover = budget - used
            batch, ubatch = _batch_for_leftover(leftover + scratch, cpu=False)
            target = "gpu_full" if not cache_type else "gpu_q8"
            why = list(reasons) + [
                f"vram:{hw.vram_total_mb}mb",
                f"budget:{budget}mb",
                f"used:{used}mb",
                f"target:{target}",
            ]
            if hw.gpu_name:
                why.append(f"gpu:{hw.gpu_name}")
            return AutofitPlan(
                ctx_size=ctx,
                n_gpu_layers=-1,
                cache_type=cache_type,
                flash_attn=_flash_ok(hw),
                batch_size=batch,
                ubatch_size=ubatch,
                threads=0,
                cache_reuse=max(0, int(cache_reuse)),
                parallel=1,
                target=target,
                vram_budget_mb=budget,
                kv_mb=kv,
                weight_mb=weight_mb,
                estimated_used_mb=used,
                reasons=tuple(why),
                hardware=hw.to_public(),
                arch=arch.to_public(),
            )
    return _plan_partial_gpu(hw, arch, weight_mb, ladder, cache_reuse, reasons)


def _plan_partial_gpu(
    hw: HardwareProbe,
    arch: ModelArch,
    weight_mb: int,
    ladder: tuple[int, ...],
    cache_reuse: int,
    reasons: list[str],
) -> AutofitPlan | None:
    budget = int(hw.vram_total_mb * _VRAM_USE_FRAC)
    overhead = _CUDA_OVERHEAD_MB
    # Estimate layers that fit after a small KV + overhead
    for cache_type in ("q8_0", "q4_0", ""):
        for ctx in ladder:
            if ctx > 8192:
                continue  # partial offload + huge ctx is the OOM classic
            kv = estimate_kv_mb(ctx, arch, cache_type or "q8_0", 1)
            remain = budget - overhead - kv - 384
            if remain < 512:
                continue
            frac = min(0.92, max(0.15, remain / max(1, weight_mb)))
            ngl = max(4, int(arch.n_layer * frac))
            if ngl >= arch.n_layer:
                ngl = -1
            used = min(budget, int(weight_mb * (1.0 if ngl < 0 else frac)) + kv + overhead + 384)
            if ngl != -1 and remain < 768:
                continue
            leftover = max(0, budget - used)
            batch, ubatch = _batch_for_leftover(leftover, cpu=False)
            why = list(reasons) + [
                f"vram:{hw.vram_total_mb}mb",
                f"partial_ngl:{ngl}",
                "target:gpu_partial",
            ]
            return AutofitPlan(
                ctx_size=ctx,
                n_gpu_layers=ngl,
                cache_type=cache_type or "q8_0",
                flash_attn=_flash_ok(hw),
                batch_size=batch,
                ubatch_size=ubatch,
                threads=0,
                cache_reuse=max(0, int(cache_reuse)),
                parallel=1,
                target="gpu_partial",
                vram_budget_mb=budget,
                kv_mb=kv,
                weight_mb=weight_mb,
                estimated_used_mb=used,
                reasons=tuple(why),
                hardware=hw.to_public(),
                arch=arch.to_public(),
            )
    return None


def _plan_cpu(
    hw: HardwareProbe,
    arch: ModelArch,
    weight_mb: int,
    ladder: tuple[int, ...],
    cache_reuse: int,
    reasons: list[str],
) -> AutofitPlan:
    ram = hw.ram_total_mb or 16_384
    # Leave the OS + browser + Remedy a lot of RAM
    budget = int(ram * 0.42)
    overhead = _CPU_OVERHEAD_MB
    chosen_ctx = _MIN_PLAN_CTX
    cache_type = "q8_0"
    for ctx in ladder:
        if ctx > 16_384:
            continue
        kv = estimate_kv_mb(ctx, arch, cache_type, 1)
        used = weight_mb + kv + overhead + 256
        if _fits(used, budget) or ram <= 0:
            chosen_ctx = ctx
            break
    else:
        chosen_ctx = _MIN_PLAN_CTX
    kv = estimate_kv_mb(chosen_ctx, arch, cache_type, 1)
    used = weight_mb + kv + overhead + 256
    why = list(reasons) + [
        f"ram:{ram}mb",
        "target:cpu",
        "no_usable_gpu" if not hw.usable_gpu else "gpu_did_not_fit",
    ]
    return AutofitPlan(
        ctx_size=chosen_ctx,
        n_gpu_layers=0,
        cache_type=cache_type,
        flash_attn=False,
        batch_size=512,
        ubatch_size=128,
        threads=_cpu_threads(hw),
        cache_reuse=max(0, int(cache_reuse)),
        parallel=1,
        target="cpu",
        vram_budget_mb=budget,
        kv_mb=kv,
        weight_mb=weight_mb,
        estimated_used_mb=used,
        reasons=tuple(why),
        hardware=hw.to_public(),
        arch=arch.to_public(),
    )


def _plan_from_last_good(
    last_good: dict[str, Any] | None,
    model: Path | str | None,
    hw: HardwareProbe,
    arch: ModelArch,
    cache_reuse: int,
) -> AutofitPlan | None:
    if not isinstance(last_good, dict):
        return None
    want = ""
    if model:
        try:
            want = str(Path(model).resolve())
        except OSError:
            want = str(model)
    got = str(last_good.get("model_path") or "")
    if not want or not got:
        return None
    if Path(got).name.lower() != Path(want).name.lower() and got.lower() != want.lower():
        return None
    prev_vram = int(last_good.get("vram_total_mb") or 0)
    if prev_vram and hw.vram_total_mb:
        # Hardware changed a lot → recompute (upgrade / different GPU)
        if abs(prev_vram - hw.vram_total_mb) > max(1024, int(prev_vram * 0.15)):
            return None
    try:
        ctx = max(_MIN_PLAN_CTX, int(last_good.get("ctx_size") or 0))
        ngl_raw = last_good.get("n_gpu_layers")
        ngl = int(ngl_raw) if ngl_raw is not None else -1
    except (TypeError, ValueError):
        return None
    cache_type = str(last_good.get("cache_type") or "")
    flash = bool(last_good.get("flash_attn", True))
    batch = int(last_good.get("batch_size") or 1024)
    ubatch = int(last_good.get("ubatch_size") or 256)
    kv = estimate_kv_mb(ctx, arch, cache_type, 1)
    return AutofitPlan(
        ctx_size=ctx,
        n_gpu_layers=ngl,
        cache_type=cache_type,
        flash_attn=flash and _flash_ok(hw) and ngl != 0,
        batch_size=batch,
        ubatch_size=ubatch,
        threads=0 if ngl != 0 else _cpu_threads(hw),
        cache_reuse=max(0, int(last_good.get("cache_reuse") or cache_reuse)),
        parallel=1,
        target=str(last_good.get("target") or "last_good"),
        vram_budget_mb=int(hw.vram_total_mb * _VRAM_USE_FRAC),
        kv_mb=kv,
        weight_mb=max(1, arch.weight_bytes // (1024 * 1024)),
        estimated_used_mb=int(last_good.get("estimated_used_mb") or 0),
        reasons=("last_good_fit", f"vram:{hw.vram_total_mb}mb"),
        hardware=hw.to_public(),
        arch=arch.to_public(),
    )


def last_good_payload(plan: AutofitPlan, model: Path | str | None, hw: HardwareProbe) -> dict[str, Any]:
    path = ""
    mtime = 0.0
    if model:
        p = Path(model)
        path = str(p)
        try:
            mtime = float(p.stat().st_mtime)
        except OSError:
            mtime = 0.0
    return {
        "model_path": path,
        "model_mtime": mtime,
        "vram_total_mb": hw.vram_total_mb,
        "ctx_size": plan.ctx_size,
        "n_gpu_layers": plan.n_gpu_layers,
        "cache_type": plan.cache_type,
        "flash_attn": plan.flash_attn,
        "batch_size": plan.batch_size,
        "ubatch_size": plan.ubatch_size,
        "cache_reuse": plan.cache_reuse,
        "target": plan.target,
        "estimated_used_mb": plan.estimated_used_mb,
    }


def plan_from_state(
    state: dict[str, Any],
    model: Path | str | None,
    *,
    hardware: HardwareProbe | None = None,
) -> AutofitPlan:
    """Build a plan from explicit user knobs (no hardware search)."""
    hw = hardware or probe_hardware()
    arch = estimate_model_arch(model)
    try:
        ctx = max(_MIN_PLAN_CTX, int(state.get("ctx_size") or 8192))
    except (TypeError, ValueError):
        ctx = 8192
    try:
        ngl_raw = state.get("n_gpu_layers")
        ngl = int(ngl_raw) if ngl_raw is not None else -1
    except (TypeError, ValueError):
        ngl = -1
    if ngl < 0 and not hw.usable_gpu:
        ngl = 0
    cache_type = str(state.get("cache_type") or "")
    flash = bool(state.get("flash_attn", True)) and _flash_ok(hw) and ngl != 0
    try:
        batch = int(state.get("batch_size") or 2048)
    except (TypeError, ValueError):
        batch = 2048
    try:
        ubatch = int(state.get("ubatch_size") or 512)
    except (TypeError, ValueError):
        ubatch = 512
    try:
        threads = int(state.get("threads") or 0)
    except (TypeError, ValueError):
        threads = 0
    try:
        reuse_raw = state.get("cache_reuse")
        reuse = int(reuse_raw) if reuse_raw is not None else 256
    except (TypeError, ValueError):
        reuse = 256
    kv = estimate_kv_mb(ctx, arch, cache_type, 1)
    weight_mb = max(1, arch.weight_bytes // (1024 * 1024)) if arch.weight_bytes else 0
    return AutofitPlan(
        ctx_size=ctx,
        n_gpu_layers=ngl,
        cache_type=cache_type,
        flash_attn=flash,
        batch_size=max(128, batch),
        ubatch_size=max(64, ubatch),
        threads=threads,
        cache_reuse=max(0, reuse),
        parallel=1,
        target="manual",
        vram_budget_mb=int((hw.vram_total_mb or 0) * _VRAM_USE_FRAC),
        kv_mb=kv,
        weight_mb=weight_mb,
        estimated_used_mb=weight_mb + kv,
        reasons=("user_locked",),
        hardware=hw.to_public(),
        arch=arch.to_public(),
    )


def should_autofit(state: dict[str, Any] | None) -> bool:
    """True when start should compute a hardware fit instead of static presets."""
    st = state if isinstance(state, dict) else {}
    if st.get("autofit_locked"):
        return False
    if st.get("autofit") is False:
        return False
    prof = str(st.get("profile") or "").strip().lower()
    if prof == "autofit":
        return True
    if st.get("autofit") is True:
        return True
    # Legacy factory: agent + 8k + all-layers → treat as autofit so existing
    # installs get the better default without a Settings click.
    if prof in ("turbo", "quality"):
        return False
    try:
        ctx = int(st.get("ctx_size") or 8192)
    except (TypeError, ValueError):
        ctx = 8192
    ngl = st.get("n_gpu_layers")
    try:
        ngl_i = int(ngl) if ngl is not None else -1
    except (TypeError, ValueError):
        ngl_i = -1
    cache = str(st.get("cache_type") or "").strip()
    return prof in ("agent", "autofit", "") and ctx == 8192 and ngl_i == -1 and not cache


def classify_start_failure(log_tail: str | None, *, exit_code: int | None = None, timed_out: bool = False) -> str:
    """Classify a failed llama-server start for the retry ladder."""
    if timed_out:
        return "timeout"
    text = (log_tail or "").lower()
    if any(m in text for m in _UNKNOWN_FLAG_MARKERS):
        return "unknown_flag"
    if any(m in text for m in _FLASH_MARKERS):
        return "flash_attn"
    if any(m in text for m in _OOM_MARKERS):
        return "oom"
    # CUDA builds often exit 1/0xC0000005 with a thin log on VRAM death
    if exit_code not in (None, 0) and not text.strip():
        return "oom"
    if exit_code not in (None, 0):
        return "crash"
    return "crash"


def snap_ctx(ctx: int) -> int:
    c = max(_MIN_PLAN_CTX, int(ctx))
    for step in _CTX_LADDER:
        if step <= c:
            return step
    return _MIN_PLAN_CTX


def downgrade_plan(plan: AutofitPlan, reason: str) -> AutofitPlan | None:
    """Walk one step down the stability ladder. None = cannot go lower."""
    extra = (f"downgrade:{reason}",)
    reasons = tuple(plan.reasons) + extra

    if reason == "unknown_flag" and plan.cache_reuse:
        return replace(plan, cache_reuse=0, reasons=reasons + ("strip_cache_reuse",))

    if reason == "flash_attn" and plan.flash_attn:
        return replace(plan, flash_attn=False, reasons=reasons + ("disable_flash",))

    # First OOM lever: quantize KV (halves cache, keeps ctx)
    if reason in ("oom", "timeout", "crash") and not plan.cache_type:
        return replace(plan, cache_type="q8_0", target="gpu_q8", reasons=reasons + ("kv_q8_0",))

    if plan.ctx_size > _MIN_PLAN_CTX:
        nxt = snap_ctx(plan.ctx_size // 2)
        if nxt >= plan.ctx_size:
            nxt = max(_MIN_PLAN_CTX, plan.ctx_size - 4096)
        if nxt < plan.ctx_size:
            return replace(plan, ctx_size=nxt, reasons=reasons + (f"ctx:{nxt}",))

    if plan.cache_type == "q8_0":
        return replace(plan, cache_type="q4_0", reasons=reasons + ("kv_q4_0",))

    if plan.n_gpu_layers == -1 or plan.n_gpu_layers > 8:
        ngl = 16 if plan.n_gpu_layers == -1 else max(0, plan.n_gpu_layers // 2)
        return replace(
            plan,
            n_gpu_layers=ngl,
            target="gpu_partial",
            reasons=reasons + (f"ngl:{ngl}",),
        )

    if plan.flash_attn:
        return replace(plan, flash_attn=False, reasons=reasons + ("disable_flash",))

    if plan.n_gpu_layers != 0:
        return replace(
            plan,
            n_gpu_layers=0,
            flash_attn=False,
            ctx_size=min(plan.ctx_size, 4096),
            cache_type=plan.cache_type or "q8_0",
            batch_size=min(plan.batch_size, 512),
            ubatch_size=min(plan.ubatch_size, 128),
            target="cpu",
            reasons=reasons + ("cpu_fallback",),
        )
    return None


def apply_plan_to_state(state: dict[str, Any], plan: AutofitPlan) -> dict[str, Any]:
    """Write plan knobs onto rmb.json state (mutates and returns *state*)."""
    state["ctx_size"] = int(plan.ctx_size)
    state["n_gpu_layers"] = int(plan.n_gpu_layers)
    state["cache_type"] = str(plan.cache_type or "")
    state["flash_attn"] = bool(plan.flash_attn)
    state["batch_size"] = int(plan.batch_size)
    state["ubatch_size"] = int(plan.ubatch_size)
    state["threads"] = int(plan.threads)
    state["cache_reuse"] = int(plan.cache_reuse)
    # ``parallel`` is an OWNER_HOST_KEY — autofit never writes it (real states
    # always carry a value via merge_state, so a conditional write would be
    # dead anyway). The plan's slot count is recorded in last_autofit.
    state["last_autofit"] = plan.to_public()
    return state


def probe_live_n_ctx(base_url: str, *, timeout: float = 1.8) -> int | None:
    """Read the physical n_ctx from a running llama-server (/props or /v1/models)."""
    from urllib.error import URLError
    from urllib.request import Request

    base = (base_url or "").rstrip("/")
    if not base:
        return None
    try:
        from remedy.core.security import is_loopback_service_url, urlopen_no_redirect
    except Exception:
        return None
    if not is_loopback_service_url(base):
        return None
    root = base[:-3] if base.endswith("/v1") else base
    urls = (root + "/props", base + "/models" if base.endswith("/v1") else base + "/v1/models")
    import json as _json

    for url in urls:
        try:
            req = Request(url, headers={"User-Agent": "RemedyAI-RMB/1.0"})
            with urlopen_no_redirect(req, timeout=timeout) as resp:
                raw = resp.read(65_536).decode("utf-8", errors="ignore")
            data = _json.loads(raw or "{}")
        except (URLError, OSError, ValueError, TimeoutError):
            continue
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        # llama-server /props
        dgs = data.get("default_generation_settings")
        if isinstance(dgs, dict):
            for key in ("n_ctx", "n_ctx_train"):
                try:
                    n = int(dgs.get(key) or 0)
                except (TypeError, ValueError):
                    n = 0
                if n >= 512:
                    return n
        try:
            n = int(data.get("n_ctx") or 0)
            if n >= 512:
                return n
        except (TypeError, ValueError):
            pass
        # OpenAI /v1/models
        rows = data.get("data") or data.get("models") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in ("context_window", "context_length", "n_ctx"):
                    try:
                        n = int(row.get(key) or 0)
                    except (TypeError, ValueError):
                        n = 0
                    if n >= 512:
                        return n
                meta_raw = row.get("meta")
                meta: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
                for key in ("n_ctx_train", "n_ctx", "context_length"):
                    try:
                        n = int(meta.get(key) or 0)
                    except (TypeError, ValueError):
                        n = 0
                    if n >= 512:
                        return n
    return None
