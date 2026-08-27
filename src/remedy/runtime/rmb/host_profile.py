"""Per-GGUF host profile — detect knobs, never hide them from the owner.

Frontier cloud is one API. Local GGUFs differ: Jinja templates, thinking
toggles, MTP slots, mmap, and whether the file even fits VRAM. Remedy
detects that from the filename + a light GGUF metadata sniff and applies
it on every Start / model switch. Every auto knob has an owner override
in rmb.json / Settings; thinking stays **on** unless the owner turns it off.
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

# Owner rmb.json keys auto-load must not clobber (Settings / rmb settings).
OWNER_HOST_KEYS = frozenset(
    {
        "use_jinja",
        "no_mmap",
        "thinking",
        "reasoning_budget",
        "enable_mtp",
        "spec_draft_n_max",
        "n_cpu_moe",
        "n_gpu_layers_draft",
        "model_draft",
        "parallel",
    }
)

# Shared numeric bounds for owner engine knobs. The desktop Settings inputs
# (sections_localModels.tsx) mirror these — change them together.
SPEC_DRAFT_N_MAX_MIN, SPEC_DRAFT_N_MAX_MAX = 1, 8
N_GPU_LAYERS_DRAFT_MIN, N_GPU_LAYERS_DRAFT_MAX = 1, 99
N_CPU_MOE_MIN, N_CPU_MOE_MAX = -1, 256
REASONING_BUDGET_MIN, REASONING_BUDGET_MAX = -1, 100_000

_THINKING_OFF_WORDS = frozenset(
    {"off", "false", "0", "no", "disable", "disabled", "none", "never"}
)
_THINKING_ON_WORDS = frozenset(
    {"", "on", "true", "1", "yes", "default", "auto", "enable", "enabled", "always"}
)


def normalize_thinking(value: Any) -> str:
    """Owner thinking switch: ``on`` (default) or ``off``."""
    raw = str(value if value is not None else "on").strip().lower()
    if raw in _THINKING_OFF_WORDS:
        return "off"
    return "on"


def thinking_value_known(value: Any) -> bool:
    """True when *value* is a recognized thinking word in either direction.

    Settings entry points validate with this so a typo ("of", "disbled")
    errors out instead of silently flipping thinking **on**.
    """
    raw = str(value if value is not None else "on").strip().lower()
    return raw in _THINKING_OFF_WORDS or raw in _THINKING_ON_WORDS


def reasoning_budget_cap(value: Any) -> int | None:
    """Owner reasoning_budget as a cap: int ≥ 0, or None when unset/unlimited.

    Single home for the "None/'' → unset, ≥0 → cap, negative → unlimited"
    rule so overlay and the request-body path cannot drift.
    """
    if value is None or str(value).strip() == "":
        return None
    try:
        n = int(value)  # ints, bools, and JSON floats like 512.0
    except (TypeError, ValueError):
        try:
            n = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
    return n if n >= 0 else None


def thinking_is_on(value: Any) -> bool:
    return normalize_thinking(value) == "on"


def owner_flag_on(value: Any, *, default: bool = True) -> bool:
    """Bool-ish owner flag. Strings like ``off`` / ``disabled`` are False.

    Shares the thinking off-vocabulary so ``enable_mtp="disabled"`` cannot
    silently evaluate ON while ``thinking="disabled"`` evaluates off.
    """
    if value is None:
        return default
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in ("", "auto"):
            return default
        return raw not in _THINKING_OFF_WORDS
    return bool(value)


def owner_flag_value_known(value: Any) -> bool:
    """True when a bool-ish owner flag value is recognized either way.

    Non-strings are always accepted (bool()-coerced); unknown strings —
    typos like ``"disbled"`` — should be rejected by the settings entry
    points instead of silently evaluating as ON.
    """
    if not isinstance(value, str):
        return True
    raw = value.strip().lower()
    return raw in _THINKING_OFF_WORDS or raw in _THINKING_ON_WORDS


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
        "model_draft": None,
        "n_gpu_layers_draft": None,
    }


def find_sibling_mtp_draft(model: Path | str | None) -> Path | None:
    """Sibling ``mtp-<stem>.gguf`` next to the main GGUF (Qwen 3.8 layout).

    The main file is a normal instruct GGUF; the draft is a separate MTP
    file beside it. Filename-only ``mtp`` on the *main* path misses this.
    """
    if not model:
        return None
    p = Path(model)
    if p.suffix.lower() != ".gguf":
        return None
    parent = p.parent
    stem = p.stem
    name = p.name
    if _MTP_NAME_RE.search(name):
        # Already the draft (or a baked-in MTP main) — don't pair with self.
        return None
    candidates = (
        parent / f"mtp-{stem}.gguf",
        parent / f"mtp-{name}",
        parent / f"{stem}-mtp.gguf",
        parent / f"{stem}.mtp.gguf",
    )
    seen: set[str] = set()
    for c in candidates:
        try:
            key = str(c.resolve()).lower()
        except OSError:
            key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if c.is_file() and c.suffix.lower() == ".gguf":
            try:
                if c.resolve() == p.resolve():
                    continue
            except OSError:
                if str(c) == str(p):
                    continue
            return c
    return None


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
    sibling_draft = find_sibling_mtp_draft(p)
    if sibling_draft is not None and not mtp:
        mtp = True
        reasons.append("sibling_draft")
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
    if sibling_draft is not None:
        try:
            if sibling_draft.is_file():
                weight_mb += max(0, int(sibling_draft.stat().st_size // (1024 * 1024)))
        except OSError:
            pass
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

    # Detection only — do not force thinking off. Owner switch is ``thinking``
    # on rmb.json (default on). overlay_owner_on_profile applies that.
    # ``always_think`` is card metadata (reasons/warnings tell the owner why);
    # since 0.41.5 the runtime switch is --reasoning / chat_template_kwargs.
    if thinking and not qwen_toggle:
        always_think = True
        reasons.append("filename_always_think")

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
            "Thinking model — hidden reasoning can run long. Turn thinking off "
            "in RMB options if short answers feel slow."
        )
    if qwen_toggle:
        warnings.append(
            "Qwen3-family — thinking is on by default. Turn it off in RMB "
            "options for faster short replies."
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
    if thinking or qwen_toggle:
        bits.append("thinking")
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
        # Both owned by overlay_owner_on_profile — detection never sets them.
        "chat_template_kwargs": None,
        "reasoning_budget": None,
        "chat_style": chat_style,
        "unfit": unfit,
        "weight_mb": weight_mb,
        "warnings": warnings,
        "reasons": reasons,
        "summary": summary,
        "model_stem": p.stem,
        "model_draft": str(sibling_draft) if sibling_draft is not None else None,
        "n_gpu_layers_draft": None,
    }


def overlay_owner_on_profile(
    profile: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply owner rmb.json knobs onto a detected host profile.

    Thinking defaults **on**. MTP / draft / MoE stay auto unless the owner
    set them. Call this before ``_build_cmd``.
    """
    out = dict(profile) if isinstance(profile, dict) else _empty_profile()
    st = state if isinstance(state, dict) else {}
    mode = normalize_thinking(st.get("thinking"))
    out["thinking_mode"] = mode
    if mode == "off":
        if out.get("qwen_thinking_toggle"):
            out["chat_template_kwargs"] = '{"enable_thinking": false}'
        else:
            out["chat_template_kwargs"] = None
        if out.get("reasoning_budget") is None:
            out["reasoning_budget"] = 0
    else:
        if out.get("qwen_thinking_toggle"):
            out["chat_template_kwargs"] = '{"enable_thinking": true}'
        else:
            out["chat_template_kwargs"] = None
        out["reasoning_budget"] = None
    raw_rb = st.get("reasoning_budget")
    if raw_rb is not None and str(raw_rb).strip() != "":
        rb = reasoning_budget_cap(raw_rb)
        if rb is not None:
            out["reasoning_budget"] = rb
        elif mode == "on":
            out["reasoning_budget"] = None

    if not owner_flag_on(st.get("enable_mtp"), default=True):
        out["mtp"] = False
        out["force_parallel_1"] = False
        out["spec_type"] = None
        out["spec_draft_n_max"] = None
        out["model_draft"] = None
        out["mtp_owner_off"] = True
        # Nothing is armed while the owner switch is off — a card recorded at
        # an MTP start must not keep claiming "armed" next to mtp=False.
        out["mtp_armed"] = False
    else:
        # Re-overlaying a card from a previous overlay: drop the stale marker.
        out.pop("mtp_owner_off", None)
    try:
        n_max = int(st.get("spec_draft_n_max") or 0)
        if n_max > 0:
            out["spec_draft_n_max"] = max(
                SPEC_DRAFT_N_MAX_MIN, min(SPEC_DRAFT_N_MAX_MAX, n_max)
            )
    except (TypeError, ValueError):
        pass
    draft = str(st.get("model_draft") or "").strip()
    if draft and not out.get("mtp_owner_off"):
        out["model_draft"] = draft
        # Owner typed this path — _build_cmd emits classic --model-draft
        # speculation for it even when the main GGUF is not MTP-named.
        # The owner MTP switch kills all speculation, so its clear above
        # stays authoritative.
        out["model_draft_owner"] = True
    else:
        out.pop("model_draft_owner", None)
    try:
        raw_dn = st.get("n_gpu_layers_draft")
        if raw_dn is not None and str(raw_dn).strip() != "" and int(raw_dn) > 0:
            out["n_gpu_layers_draft"] = max(
                N_GPU_LAYERS_DRAFT_MIN, min(N_GPU_LAYERS_DRAFT_MAX, int(raw_dn))
            )
    except (TypeError, ValueError):
        pass
    try:
        moe = int(st["n_cpu_moe"]) if st.get("n_cpu_moe") is not None else 0
        if moe > 0:
            out["n_cpu_moe"] = moe
            out["n_cpu_moe_owner"] = True
        elif moe < 0:
            # Owner said "keep every expert on the GPU": emit no --n-cpu-moe
            # and suppress the catalog fallback (owner marker does that).
            out["n_cpu_moe"] = 0
            out["n_cpu_moe_owner"] = True
        else:
            # 0 = auto — drop a stale marker from a previously overlaid card.
            out.pop("n_cpu_moe_owner", None)
    except (TypeError, ValueError):
        pass

    # Summary: rewrite the state-bearing bits ("thinking", "MTP") from flags
    # instead of substring surgery — exact-token matching cannot corrupt other
    # bits and it heals cards an older overlay annotated wrongly (e.g.
    # "instruct · jinja · thinking off" on a model with no thinking knob).
    tokens = [
        t for t in (s.strip() for s in str(out.get("summary") or "").split("·")) if t
    ]
    has_think_knob = bool(
        out.get("thinking") or out.get("qwen_thinking_toggle") or out.get("always_think")
    )
    think_token = ("thinking off" if mode == "off" else "thinking") if has_think_knob else None
    mtp_token = None
    if out.get("mtp_owner_off"):
        mtp_token = "MTP off"
    elif out.get("mtp"):
        mtp_token = "MTP"

    def _rewrite(toks: list[str], variants: tuple[str, ...], want: str | None) -> list[str]:
        rebuilt: list[str] = []
        placed = False
        for t in toks:
            if t in variants:
                if want is not None and not placed:
                    rebuilt.append(want)
                    placed = True
                # duplicates and unwanted state tokens are dropped
            else:
                rebuilt.append(t)
        if want is not None and not placed:
            rebuilt.append(want)
        return rebuilt

    tokens = _rewrite(tokens, ("thinking", "thinking off"), think_token)
    tokens = _rewrite(tokens, ("MTP", "MTP off"), mtp_token)
    out["summary"] = " · ".join(tokens)
    return out


def apply_host_profile_to_state(
    state: dict[str, Any],
    profile: dict[str, Any] | None,
    *,
    preserve: set[str] | None = None,
) -> dict[str, Any]:
    """Write auto-load knobs onto rmb.json state. Does not touch ctx / ngl.

    ``preserve`` is a set of keys the owner set (do not overwrite). Pass
    ``OWNER_HOST_KEYS`` on start / model switch so auto-load never clobbers
    Settings. ``use_jinja`` is special: the owner-set flag is
    ``use_jinja_owner`` (written by apply_rmb_settings on an explicit patch);
    without it, detection keeps correcting the value on every start/switch —
    a stale auto-written False must not break the chat template of the next
    instruct GGUF, and a template-less base GGUF must not get --jinja.
    """
    if not isinstance(state, dict):
        return state
    prof = profile if isinstance(profile, dict) else _empty_profile()
    keep = preserve or set()
    if "use_jinja" not in keep or not state.get("use_jinja_owner"):
        state["use_jinja"] = bool(prof.get("use_jinja", True))
    if "no_mmap" not in keep:
        state["no_mmap"] = bool(prof.get("no_mmap", False))
    if prof.get("force_parallel_1") and "parallel" not in keep:
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
