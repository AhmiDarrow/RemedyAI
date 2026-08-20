"""Per-GGUF host profile — auto-load knobs users should not have to know.

Frontier cloud is one API. Local GGUFs differ: Jinja templates, thinking
toggles, MTP slots, mmap, and whether the file even fits VRAM. Remedy
detects that from the filename + a light GGUF metadata sniff and applies
it on every Start / model switch.
"""

from __future__ import annotations

import logging
import re
import struct
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MTP_NAME_RE = re.compile(r"(?:^|[-_.\s])mtp(?:[-_.\s]|$)", re.IGNORECASE)
_CODER_NAME_RE = re.compile(
    r"(?:^|[-_.\s])(coder|coding|code)(?:[-_.\s]|$)", re.IGNORECASE
)
_QWEN3_NAME_RE = re.compile(r"qwen\s*3|qwopus|qwen3", re.IGNORECASE)
_THINKING_NAME_RE = re.compile(
    r"(?:^|[-_.\s])(r1|qwq|thinking|reasoner|reasoning)(?:[-_.\s]|$)",
    re.IGNORECASE,
)
_VISION_NAME_RE = re.compile(
    r"(?:^|[-_.\s])(vl|vision|mmproj)(?:[-_.\s]|$)", re.IGNORECASE
)
_INSTRUCT_NAME_RE = re.compile(
    r"(?:^|[-_.\s])(instruct|chat|it|inst)(?:[-_.\s]|$)", re.IGNORECASE
)
_BASE_NAME_RE = re.compile(r"(?:^|[-_.\s])(base|pt|pretrain)(?:[-_.\s]|$)", re.IGNORECASE)
_SIZE_RE = re.compile(
    r"(?:^|[^0-9])(0?\.5|1\.5|[0-9]+(?:\.[0-9]+)?)\s*b(?:[^0-9]|$)",
    re.IGNORECASE,
)

_GGUF_UINT8, _GGUF_INT8 = 0, 1
_GGUF_UINT16, _GGUF_INT16 = 2, 3
_GGUF_UINT32, _GGUF_INT32 = 4, 5
_GGUF_FLOAT32, _GGUF_BOOL = 6, 7
_GGUF_STRING, _GGUF_ARRAY = 8, 9
_GGUF_UINT64, _GGUF_INT64, _GGUF_FLOAT64 = 10, 11, 12

_CHAT_TEMPLATE_KEYS = (
    "tokenizer.chat_template",
    "tokenizer.ggml.chat_template",
)
_NAME_KEYS = ("general.name", "general.basename", "general.architecture")
_MAX_TEMPLATE_CHARS = 200_000


def _empty_profile() -> dict[str, Any]:
    return {
        "mtp": False,
        "coder": False,
        "qwen3_family": False,
        "thinking": False,
        "always_think": False,
        "qwen_thinking_toggle": False,
        "vision": False,
        "base_model": False,
        "instruct": False,
        "force_parallel_1": False,
        "spec_type": None,
        "spec_draft_n_max": None,
        "use_jinja": True,
        "no_mmap": False,
        "chat_template_kwargs": None,
        "reasoning_budget": None,
        "chat_style": "instruct",
        "unfit": False,
        "weight_mb": 0,
        "warnings": [],
        "reasons": [],
        "summary": "default",
        "model_stem": "",
    }


def read_gguf_chat_signals(model: Path | str | None) -> dict[str, Any]:
    """Sniff chat template + name from GGUF KV. Empty dict on any failure."""
    if not model:
        return {}
    p = Path(model)
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
                want = key in _CHAT_TEMPLATE_KEYS or key in _NAME_KEYS
                if want and vtype == _GGUF_STRING:
                    val = _read_gguf_string(f)
                    if key in _CHAT_TEMPLATE_KEYS:
                        out["chat_template"] = val[:_MAX_TEMPLATE_CHARS]
                    else:
                        out.setdefault("gguf_name", val)
                else:
                    _skip_gguf_value(f, int(vtype))
                if "chat_template" in out and "gguf_name" in out:
                    break
    except Exception:
        logger.debug("GGUF chat-signal parse failed for %s", model, exc_info=True)
        return {}
    tmpl = str(out.get("chat_template") or "")
    low = tmpl.lower()
    out["has_template"] = bool(tmpl.strip())
    out["enable_thinking_knob"] = "enable_thinking" in low
    out["has_think_tags"] = "<think>" in low or "</think>" in low
    return out


def detect_gguf_host_profile(
    model: Path | str | None,
    *,
    hardware: dict[str, Any] | None = None,
    sniff_template: bool = True,
) -> dict[str, Any]:
    """Infer llama-server + Remedy chat knobs from a GGUF path/name.

    Returns a stable dict used by ``_build_cmd``, start, settings switch,
    and status. Hardware is optional (vram_total_mb) for the unfit warning.
    """
    if model is None:
        return _empty_profile()
    p = Path(model)
    name = f"{p.name} {p.stem}".lower()
    reasons: list[str] = []
    warnings: list[str] = []

    mtp = bool(_MTP_NAME_RE.search(name)) or "multi-token" in name or "multitoken" in name
    if mtp:
        reasons.append("filename_mtp")
    coder = bool(_CODER_NAME_RE.search(name))
    if coder:
        reasons.append("filename_coder")
    qwen3 = bool(_QWEN3_NAME_RE.search(name))
    if qwen3:
        reasons.append("filename_qwen3_family")
    thinking = bool(_THINKING_NAME_RE.search(name))
    if thinking:
        reasons.append("filename_thinking")
    vision = bool(_VISION_NAME_RE.search(name))
    if vision:
        reasons.append("filename_vision")
    instruct = bool(_INSTRUCT_NAME_RE.search(name))
    if instruct:
        reasons.append("filename_instruct")
    base_model = bool(_BASE_NAME_RE.search(name)) and not instruct
    if base_model:
        reasons.append("filename_base")

    signals = read_gguf_chat_signals(p) if sniff_template else {}
    qwen_toggle = bool(signals.get("enable_thinking_knob")) or qwen3
    always_think = False
    # Many instruct templates mention <think> as an optional block. Only treat
    # as always-on thinking when the filename is a reasoner (R1 / QwQ / …)
    # and the template has no enable_thinking switch.
    if (
        thinking
        and signals.get("has_think_tags")
        and not signals.get("enable_thinking_knob")
    ):
        always_think = True
        reasons.append("template_always_think")
    if signals.get("enable_thinking_knob"):
        reasons.append("template_thinking_toggle")
        qwen_toggle = True
    if signals.get("has_template"):
        reasons.append("gguf_chat_template")
    elif p.is_file() and p.stat().st_size > 1024:
        # Real file with no embedded template — treat as completion/base.
        if not instruct and not qwen3:
            base_model = True
            reasons.append("no_chat_template")

    # Conservative draft length: slightly higher for larger MTP weights
    draft_n = 2
    weight_mb = 0
    try:
        if p.is_file():
            weight_mb = max(0, int(p.stat().st_size // (1024 * 1024)))
    except OSError:
        weight_mb = 0
    if mtp:
        size_gb = weight_mb / 1024.0
        if size_gb >= 12 or re.search(r"\b(14b|27b|32b|35b|70b)\b", name):
            draft_n = 3

    # Modern instruct / Qwen / thinking GGUFs need the embedded Jinja
    # template. Base/completion files still get jinja when a template exists.
    use_jinja = True
    if base_model and not signals.get("has_template") and not qwen3:
        use_jinja = bool(signals.get("has_template"))

    # mmap is the right default (faster load, less RAM). no_mmap was a leftover
    # that made large GGUFs crawl.
    no_mmap = False

    chat_template_kwargs: str | None = None
    reasoning_budget: int | None = None
    if qwen_toggle:
        # Qwen3/3.5/3.6 default to a hidden <think> block that can run for
        # thousands of tokens. Remedy turns it off so chat/tools stay fast.
        chat_template_kwargs = '{"enable_thinking": false}'
        reasons.append("thinking_off_kwargs")
    if thinking and not qwen_toggle:
        # R1 / QwQ always think — cap (0 = disable) so 1+1 is not minutes.
        reasoning_budget = 0
        reasons.append("reasoning_budget_0")
        always_think = True

    if base_model:
        warnings.append(
            "This looks like a base (pretrain) GGUF — chat and tools will be weaker "
            "than an instruct/coder file."
        )
    if vision:
        warnings.append(
            "Vision GGUF loaded as text unless a multimodal projector is set."
        )
    if thinking and not qwen_toggle:
        warnings.append(
            "Thinking model — Remedy caps hidden reasoning so short answers stay fast."
        )
    if qwen_toggle:
        warnings.append(
            "Qwen3-family — Remedy turns off thinking mode so replies are direct."
        )

    unfit = False
    vram_mb = 0
    if isinstance(hardware, dict):
        try:
            vram_mb = int(hardware.get("vram_total_mb") or 0)
        except (TypeError, ValueError):
            vram_mb = 0
    if vram_mb >= 2048 and weight_mb > 0:
        budget = int(vram_mb * 0.82)
        if weight_mb + 900 > budget:
            unfit = True
            reasons.append("weights_exceed_vram")
            warnings.append(
                f"This GGUF is ~{weight_mb / 1024:.1f} GB and the GPU has "
                f"{vram_mb / 1024:.1f} GB — Remedy will partial-offload with a "
                "smaller window. A smaller quant will feel much faster."
            )

    if vision:
        chat_style = "vision"
    elif base_model:
        chat_style = "base"
    elif thinking and not qwen_toggle:
        chat_style = "thinking"
    else:
        chat_style = "instruct"

    bits: list[str] = []
    if qwen3:
        bits.append("Qwen3")
    if coder:
        bits.append("coder")
    elif instruct and not base_model and not vision:
        bits.append("instruct")
    if vision:
        bits.append("vision")
    if mtp:
        bits.append("MTP")
    if thinking and not qwen_toggle:
        bits.append("thinking")
    elif qwen_toggle:
        bits.append("thinking off")
    if base_model:
        bits.append("base")
    if use_jinja:
        bits.append("jinja")
    if unfit:
        bits.append("partial GPU")
    summary = " · ".join(bits) if bits else "instruct · jinja"

    return {
        "mtp": mtp,
        "coder": coder,
        "qwen3_family": qwen3,
        "thinking": thinking,
        "always_think": always_think,
        "qwen_thinking_toggle": qwen_toggle,
        "vision": vision,
        "base_model": base_model,
        "instruct": instruct or (not base_model and not vision),
        "force_parallel_1": mtp,
        "spec_type": "draft-mtp" if mtp else None,
        "spec_draft_n_max": draft_n if mtp else None,
        "use_jinja": use_jinja,
        "no_mmap": no_mmap,
        "chat_template_kwargs": chat_template_kwargs,
        "reasoning_budget": reasoning_budget,
        "chat_style": chat_style,
        "unfit": unfit,
        "weight_mb": weight_mb,
        "warnings": warnings,
        "reasons": reasons,
        "summary": summary,
        "model_stem": p.stem,
    }


def apply_host_profile_to_state(
    state: dict[str, Any],
    profile: dict[str, Any] | None,
    *,
    preserve: set[str] | None = None,
) -> dict[str, Any]:
    """Write auto-load knobs onto rmb.json state. Does not touch ctx / ngl.

    ``preserve`` is a set of keys the user just set in Settings (do not overwrite).
    """
    if not isinstance(state, dict):
        return state
    prof = profile if isinstance(profile, dict) else _empty_profile()
    keep = preserve or set()
    if "use_jinja" not in keep:
        state["use_jinja"] = bool(prof.get("use_jinja", True))
    if "no_mmap" not in keep:
        state["no_mmap"] = bool(prof.get("no_mmap", False))
    if prof.get("force_parallel_1"):
        state["parallel"] = 1
    # Merge, keep runtime-only keys (mtp_armed) already on disk
    prev_raw = state.get("host_auto")
    prev: dict[str, Any] = prev_raw if isinstance(prev_raw, dict) else {}
    keep_runtime = {
        k: prev[k]
        for k in ("mtp_armed", "mtp_soft_disabled", "binary_supports_mtp", "cmd_flags")
        if k in prev
    }
    state["host_auto"] = {**prof, **keep_runtime}
    return state


def model_switch_should_refit(state: dict[str, Any] | None) -> bool:
    """True when a new GGUF should re-run Autofit (forget the previous file's window)."""
    st = state if isinstance(state, dict) else {}
    prof = str(st.get("profile") or "autofit").strip().lower()
    return prof in ("autofit", "agent", "")


def _read_gguf_string(f) -> str:
    (n,) = struct.unpack("<Q", f.read(8))
    if n > 2_000_000:
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


def _size_label(name: str) -> str:
    m = _SIZE_RE.search(name)
    return (m.group(1) + "b") if m else ""
