"""Unified local-mind llama-server command builder and autofit.

One host serves Qwen3.5-4B (text + vision via --mmproj) for vision, nano,
helper, dreams, metabolism, workers, and media digests. RMB remains
optional extra muscle for larger GGUFs and no longer evicts this host.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Placeholder until the fine-tuned GGUF is hashed. Installer skips verify.
TODO_SHA256 = "TODO"

DEFAULT_SLOTS = 2
DEFAULT_CTX = 8192
DEFAULT_IMAGE_MAX_TOKENS = 1024
DEFAULT_CACHE_RAM_MIB = 4096
DEFAULT_SPEC = "auto"
DEFAULT_KV = "q8_0"
DEFAULT_QUANT = "auto"


def _knob(key: str, default: Any, cfg: dict[str, Any] | None = None) -> Any:
    try:
        from remedy.core.organism_knobs import resolve

        val = resolve(key, cfg=cfg)
        return default if val is None else val
    except Exception:
        return default


def _knob_bool(key: str, default: bool, cfg: dict[str, Any] | None = None) -> bool:
    try:
        from remedy.core.organism_knobs import resolve_bool

        return bool(resolve_bool(key, cfg=cfg))
    except Exception:
        return default


def _knob_int(key: str, default: int, cfg: dict[str, Any] | None = None) -> int:
    try:
        from remedy.core.organism_knobs import resolve_int

        return int(resolve_int(key, cfg=cfg))
    except Exception:
        return default


def total_ram_gb() -> float:
    try:
        if os.path.exists("/proc/meminfo"):
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
    except Exception:
        logger.debug("meminfo read failed", exc_info=True)
    try:
        from remedy.runtime.rmb.autofit import probe_ram

        total_mb, _avail_mb = probe_ram()
        if total_mb > 0:
            return total_mb / 1024.0
    except Exception:
        logger.debug("ram probe failed", exc_info=True)
    return 16.0


def gpu_vram_gb() -> float | None:
    try:
        from remedy.runtime.gpu_probe import probe_gpus

        # Best single device (dedicated first), in GB.
        prim = probe_gpus().primary
        if prim is None or prim.vram_total_mb <= 0:
            return None
        return prim.vram_total_mb / 1024.0
    except Exception:
        return None


def select_quant(cfg: dict[str, Any] | None = None) -> str:
    """Return 'q6_k' (default) or 'q4_k_m' for weak machines."""
    raw = str(_knob("mind_quant", DEFAULT_QUANT, cfg) or "auto").strip().lower()
    if raw in ("q4_k_m", "q4", "q4km"):
        return "q4_k_m"
    if raw in ("q6_k", "q6", "q6k"):
        return "q6_k"
    ram = total_ram_gb()
    vram = gpu_vram_gb()
    # CPU-only or tight RAM: Q4. 12 GB GPU (3080 class) keeps Q6.
    if ram and ram < 12:
        return "q4_k_m"
    if vram is not None and vram < 6:
        return "q4_k_m"
    return "q6_k"


def model_files_for_quant(quant: str) -> tuple[str, str]:
    from remedy.runtime.catalog import get_model_spec

    spec = get_model_spec()
    if quant == "q4_k_m" and getattr(spec, "fallback_model_file", ""):
        return spec.fallback_model_file, spec.mmproj_file
    return spec.model_file, spec.mmproj_file


def binary_help(binary: Path | str | None) -> bytes:
    if not binary:
        return b""
    path = Path(str(binary))
    if not path.is_file():
        return b""
    try:
        from remedy.execution.process import run_hidden

        proc = run_hidden([str(path), "--help"], timeout=8)
        out = (proc.stdout or b"") + (proc.stderr or b"")
        if isinstance(out, str):
            return out.encode("utf-8", errors="replace")
        return out
    except Exception:
        try:
            import subprocess

            proc = subprocess.run(
                [str(path), "--help"],
                capture_output=True,
                timeout=8,
                check=False,
            )
            return (proc.stdout or b"") + (proc.stderr or b"")
        except Exception:
            return b""


def _has(help_blob: bytes, *needles: str) -> bool:
    if not help_blob:
        return True  # optimistic when we cannot probe; pin is new enough
    low = help_blob.lower()
    return any(n.encode("utf-8").lower() in low for n in needles)


def _gguf_has_mtp(model_path: str | Path | None) -> bool:
    """Cheap filename / sidecar heuristic; no full GGUF parse required."""
    if not model_path:
        return False
    p = Path(str(model_path))
    name = p.name.lower()
    if "mtp" in name:
        return True
    sibling = p.with_name("mtp-" + p.name)
    if sibling.is_file():
        return True
    # Fine-tuned export is asked to keep MTP tensors inside the main GGUF.
    # When the catalog marks has_mtp, trust it.
    try:
        from remedy.runtime.catalog import get_model_spec

        return bool(getattr(get_model_spec(), "has_mtp", False))
    except Exception:
        return False


def resolve_spec_type(
    model_path: str | Path | None = None,
    *,
    help_blob: bytes = b"",
    cfg: dict[str, Any] | None = None,
    allow_mtp: bool = True,
) -> str | None:
    """Return a --spec-type value, or None to omit speculative decoding."""
    raw = str(_knob("mind_spec", DEFAULT_SPEC, cfg) or "auto").strip().lower()
    if raw in ("off", "none", "false", "0"):
        return None
    if raw in ("draft-mtp", "mtp"):
        chosen = "draft-mtp"
    elif raw in ("ngram-mod", "ngram"):
        chosen = "ngram-mod"
    else:
        # auto
        if allow_mtp and _gguf_has_mtp(model_path) and _has(help_blob, "draft-mtp"):
            chosen = "draft-mtp"
        elif _has(help_blob, "ngram-mod"):
            chosen = "ngram-mod"
        else:
            return None
    if chosen == "draft-mtp" and not allow_mtp:
        chosen = "ngram-mod" if _has(help_blob, "ngram-mod") else None
        return chosen
    return chosen


def mind_n_gpu_layers(
    *,
    requested: int | None = None,
    rmb_running: bool = False,
    cfg: dict[str, Any] | None = None,
    plan: Any | None = None,
) -> int:
    """GPU layers for the mind host.

    When local muscle is using the GPU, the VRAM planner decides whether
    the mind keeps its layers, shares a split, or yields to CPU (mmap).
    ``mind_keep_gpu`` / ``muscle_gpu_policy=keep_mind`` force a full mind
    reservation. A caller that already computed a plan should pass it so
    we do not probe hardware twice.
    """
    if requested is not None and int(requested) >= 0 and not rmb_running:
        return int(requested)
    if rmb_running:
        if plan is None:
            try:
                from remedy.runtime.rmb.coresidence import plan_coresidence

                plan = plan_coresidence(cfg=cfg, rmb_running=True)
            except Exception:
                plan = None
        if plan is not None:
            return int(plan.mind_n_gpu_layers)
        if not _knob_bool("mind_keep_gpu", False, cfg):
            return 0
    if requested is not None:
        return int(requested)
    return -1


def build_mind_server_command(
    binary: Path | str,
    model_path: Path | str,
    mmproj_path: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8740,
    n_gpu_layers: int = -1,
    cfg: dict[str, Any] | None = None,
    help_blob: bytes | None = None,
    allow_mtp: bool = True,
) -> list[str]:
    """Build a llama-server argv for the unified mind host."""
    blob = help_blob if help_blob is not None else binary_help(binary)
    slots = _knob_int("mind_slots", DEFAULT_SLOTS, cfg)
    slots = max(1, min(8, int(slots or DEFAULT_SLOTS)))
    ctx = _knob_int("mind_ctx", DEFAULT_CTX, cfg)
    ctx = max(2048, min(32768, int(ctx or DEFAULT_CTX)))
    # llama.cpp shares -c across slots; keep enough room per slot.
    # With --kv-unified-per-slot (newer builds) -c can be the per-slot cap.
    image_max = _knob_int("mind_image_max_tokens", DEFAULT_IMAGE_MAX_TOKENS, cfg)
    image_max = max(64, min(4096, int(image_max or DEFAULT_IMAGE_MAX_TOKENS)))
    kv = str(_knob("mind_kv_cache", DEFAULT_KV, cfg) or DEFAULT_KV).strip().lower()
    if kv not in ("q8_0", "f16", "bf16", "f8_e4m3"):
        kv = DEFAULT_KV
    cache_ram = _knob_int("mind_cache_ram_mib", DEFAULT_CACHE_RAM_MIB, cfg)
    draft_n = _knob_int("mind_spec_draft_n", 3, cfg)
    draft_n = max(1, min(8, int(draft_n or 3)))

    cmd: list[str] = [
        str(binary),
        "-m",
        str(model_path),
        "--mmproj",
        str(mmproj_path),
        "--host",
        str(host),
        "--port",
        str(int(port)),
        "--ctx-size",
        str(ctx),
        "-ngl",
        str(int(n_gpu_layers)),
    ]
    if slots > 0 and _has(blob, "--parallel", "-np"):
        cmd.extend(["--parallel", str(slots)])
        if _has(blob, "--kv-unified-per-slot"):
            cmd.extend(["--kv-unified-per-slot", str(ctx)])
            # Total context must cover every slot when per-slot cap is set.
            # Some builds still want -c as the aggregate; leave ctx as the
            # per-slot value when the flag exists.

    if _knob_bool("mind_flash_attn", True, cfg) and _has(blob, "--flash-attn", "-fa"):
        # New builds take `--flash-attn on`; older ones take a bare `-fa`.
        if b"--flash-attn" in (blob or b"") or not blob:
            cmd.extend(["--flash-attn", "on"])
        else:
            cmd.append("-fa")

    if _has(blob, "--jinja"):
        cmd.append("--jinja")

    if not _knob_bool("mind_thinking", False, cfg) and _has(
        blob, "--chat-template-kwargs"
    ):
        cmd.extend(["--chat-template-kwargs", '{"enable_thinking": false}'])

    if _knob_bool("mind_cache_prompt", True, cfg):
        if _has(blob, "--cache-prompt"):
            cmd.append("--cache-prompt")
        if cache_ram and _has(blob, "--cache-ram"):
            cmd.extend(["--cache-ram", str(int(cache_ram))])
        if _has(blob, "--cache-idle-slots"):
            cmd.append("--cache-idle-slots")

    if _knob_bool("mind_mmap", True, cfg) and _has(blob, "--mmap"):
        cmd.append("--mmap")

    if kv and _has(blob, "--cache-type-k"):
        cmd.extend(["--cache-type-k", kv, "--cache-type-v", kv])

    if _has(blob, "--image-max-tokens"):
        cmd.extend(["--image-max-tokens", str(image_max)])

    spec = resolve_spec_type(
        model_path, help_blob=blob, cfg=cfg, allow_mtp=allow_mtp
    )
    if spec and _has(blob, "--spec-type"):
        cmd.extend(["--spec-type", spec])
        if _has(blob, "--spec-draft-n-max"):
            cmd.extend(["--spec-draft-n-max", str(draft_n)])

    return cmd


def command_fallbacks(cmd: list[str]) -> list[list[str]]:
    """Ordered softer command lines if the first spawn exits early.

    MTP + mmproj + parallel can still fail on some builds; drop MTP,
    then all speculative flags, then flash-attn / kv quant.
    """
    variants: list[list[str]] = []

    def _without(src: list[str], flags: set[str]) -> list[str]:
        out: list[str] = []
        skip_next = False
        for i, tok in enumerate(src):
            if skip_next:
                skip_next = False
                continue
            if tok in flags:
                # flags that take a value
                if tok in {
                    "--spec-type",
                    "--spec-draft-n-max",
                    "--cache-type-k",
                    "--cache-type-v",
                    "--flash-attn",
                } and i + 1 < len(src) and not src[i + 1].startswith("-"):
                    skip_next = True
                continue
            out.append(tok)
        return out

    if "--spec-type" in cmd:
        variants.append(_without(cmd, {"--spec-type", "--spec-draft-n-max"}))
        # ngram-mod instead of mtp
        soft = _without(cmd, {"--spec-type", "--spec-draft-n-max"})
        # insert ngram-mod before trailing args
        soft.extend(["--spec-type", "ngram-mod"])
        variants.insert(0, soft)
    if "--flash-attn" in cmd or "-fa" in cmd:
        variants.append(_without(cmd, {"--flash-attn", "-fa"}))
    if "--cache-type-k" in cmd:
        variants.append(_without(cmd, {"--cache-type-k", "--cache-type-v"}))
    # unique, skip identical to original
    seen = {tuple(cmd)}
    uniq: list[list[str]] = []
    for v in variants:
        key = tuple(v)
        if key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq
