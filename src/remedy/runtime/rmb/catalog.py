"""RMB chat model catalog — coding / tool-use GGUFs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RmbModelSpec:
    id: str
    name: str
    # Prefer local path pattern; download optional later
    filename: str
    hf_repo: str | None
    approx_gb: float
    n_ctx_recommend: int
    notes: str
    # Size tag for context window heuristics
    size_label: str = "7b"
    # MoE experts to keep on the CPU. A 3B-active model runs at near-dense
    # speed with its experts in system RAM, so a 35B fits a 12GB card using
    # ~4GB of VRAM. 0 = dense model, everything on GPU.
    n_cpu_moe: int = 0
    active_b: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "filename": self.filename,
            "hf_repo": self.hf_repo,
            "approx_gb": self.approx_gb,
            "n_ctx_recommend": self.n_ctx_recommend,
            "notes": self.notes,
            "size_label": self.size_label,
            "n_cpu_moe": self.n_cpu_moe,
            "active_b": self.active_b,
        }


# Default product model for agent coding on 12GB: Qwen3.5-9B (Q6_K).
# Chosen on measurements against the agent suite (scripts/rig), RTX 3080 12GB:
#
#   Qwen3.5-9B Q6_K     86.1 tok/s, 8.1GB VRAM, best local score
#   Qwen3-14B  Q4_K_M   68.7 tok/s, 11.4GB VRAM, ~4x the tool calls per task
#   Qwen3.6-35B-A3B Q4  31.5 tok/s, 3.7GB VRAM, but 204s for one list_dir -
#                       a 35B pays full prefill on every ReAct step
#
# Bigger is not better here: prompt eval dominates an agent loop, and a 9B at
# 6-bit holds tool-call structure better than a larger model at 4-bit. Quant
# damage lands on structured output first, which is exactly what a tool loop
# depends on.
RMB_MODELS: dict[str, RmbModelSpec] = {
    "qwen35-9b": RmbModelSpec(
        id="qwen35-9b",
        name="Qwen3.5 9B (Q6_K)",
        filename="Qwen3.5-9B-Q6_K.gguf",
        hf_repo="unsloth/Qwen3.5-9B-GGUF",
        approx_gb=7.5,
        n_ctx_recommend=16384,
        notes=(
            "Default RMB chat model - fits a 12GB card whole at 6-bit, so it "
            "keeps tool-call structure a 4-bit model loses. Fastest of the "
            "measured options and the strongest on the agent suite."
        ),
        size_label="9b",
    ),
    "qwen36-35b-a3b": RmbModelSpec(
        id="qwen36-35b-a3b",
        name="Qwen3.6 35B-A3B (Q4_K_XL)",
        filename="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
        hf_repo="unsloth/Qwen3.6-35B-A3B-GGUF",
        approx_gb=20.8,
        n_ctx_recommend=16384,
        notes=(
            "MoE with 3B active parameters; experts run on the CPU, so it "
            "needs only ~4GB VRAM and ~21GB RAM. Slower per step than the 9B "
            "(prefill cost) and degrades when the CPU is busy - pick it when "
            "VRAM is scarce or another model needs the GPU."
        ),
        size_label="35b",
        n_cpu_moe=99,
        active_b="3b",
    ),
    "qwen25-coder-7b": RmbModelSpec(
        id="qwen25-coder-7b",
        name="Qwen2.5 Coder 7B (Q4_K_M)",
        filename="Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        hf_repo="Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        approx_gb=4.7,
        n_ctx_recommend=8192,
        notes=(
            "Default RMB chat model — strong coding + tools. "
            "Place the GGUF under ~/.remedy/rmb/models/ or point Settings at a path."
        ),
        size_label="7b",
    ),
    "qwen25-coder-14b": RmbModelSpec(
        id="qwen25-coder-14b",
        name="Qwen2.5 Coder 14B (Q4_K_M)",
        filename="Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf",
        hf_repo="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        approx_gb=9.0,
        n_ctx_recommend=8192,
        notes="Higher quality when VRAM allows (~10GB+ free for weights).",
        size_label="14b",
    ),
    "qwen25-7b": RmbModelSpec(
        id="qwen25-7b",
        name="Qwen2.5 Instruct 7B (Q4_K_M)",
        filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        hf_repo="Qwen/Qwen2.5-7B-Instruct-GGUF",
        approx_gb=4.7,
        n_ctx_recommend=8192,
        notes="General instruct; prefer Coder for agent tool chains.",
        size_label="7b",
    ),
}

DEFAULT_RMB_MODEL_ID = "qwen35-9b"

# Profiles → llama-server knobs (ctx / sampling hints for UI)
# ctx_size 0 on autofit = compute from VRAM/RAM + GGUF at start.
RMB_PROFILES: dict[str, dict[str, Any]] = {
    "autofit": {
        "label": "Autofit (recommended)",
        "ctx_size": 0,
        "n_gpu_layers": -1,
        "blurb": "Largest stable context that fits this GPU/RAM. Default.",
    },
    "agent": {
        "label": "Agent",
        "ctx_size": 8192,
        "n_gpu_layers": -1,
        "blurb": "Fixed 8k window — long tool chains on 12GB class GPUs.",
    },
    "turbo": {
        "label": "Turbo",
        "ctx_size": 4096,
        "n_gpu_layers": -1,
        "blurb": "Shorter context, snappier turns.",
    },
    "quality": {
        "label": "Quality",
        "ctx_size": 16384,
        "n_gpu_layers": -1,
        "blurb": "Fixed 16k window (may OOM on small GPUs — prefer Autofit).",
    },
}


def catalog_public() -> dict[str, Any]:
    return {
        "default_model_id": DEFAULT_RMB_MODEL_ID,
        "models": [m.to_public() for m in RMB_MODELS.values()],
        "profiles": {
            k: {"id": k, **v} for k, v in RMB_PROFILES.items()
        },
        "brand": "RMB",
        "brand_full": "Remedy Muscle Bridge",
        "engine": "llama.cpp (llama-server)",
        "default_port": 8787,
        "note": (
            "RMB is Remedy's local agent host powered by llama.cpp. "
            "The retired custom RMB4 format is not used."
        ),
    }


def get_model_spec(model_id: str | None) -> RmbModelSpec:
    mid = (model_id or DEFAULT_RMB_MODEL_ID).strip() or DEFAULT_RMB_MODEL_ID
    if mid not in RMB_MODELS:
        return RMB_MODELS[DEFAULT_RMB_MODEL_ID]
    return RMB_MODELS[mid]


def catalog_id_from_hint(hint: str | None) -> str | None:
    """Map catalog id, GGUF filename, stem, or status-bar model id → catalog id.

    Status bar uses stems like ``Qwen2.5-Coder-14B-Instruct-Q4_K_M``;
    Settings catalog uses ``qwen25-coder-14b``.
    """
    raw = (hint or "").strip()
    if not raw:
        return None
    h = raw.lower().replace("\\", "/").split("/")[-1]
    if h.endswith(".gguf"):
        h = h[:-5]
    # Exact catalog id
    if h in RMB_MODELS:
        return h
    # Exact filename stem
    for mid, spec in RMB_MODELS.items():
        stem = spec.filename.lower().replace(".gguf", "")
        if h == stem or h == spec.filename.lower():
            return mid
    # Fuzzy: size label + family (coder vs instruct) from free-form names
    # e.g. Qwen2.5-Coder-14B-Instruct-heretic.i1-Q4_K_M
    import re

    size_m = re.search(r"(?:^|[^0-9])(7b|9b|12b|14b|27b|32b|35b|72b)(?:[^0-9]|$)", h, re.I)
    size = (size_m.group(1) if size_m else "").lower()
    wants_coder = "coder" in h
    if not size:
        return None
    candidates = [
        mid
        for mid, spec in RMB_MODELS.items()
        if (spec.size_label or "").lower() == size
        and (("coder" in mid) == wants_coder or ("coder" in spec.filename.lower()) == wants_coder)
    ]
    if len(candidates) == 1:
        return candidates[0]
    # Prefer coder when name has coder
    if wants_coder:
        for mid in candidates:
            if "coder" in mid:
                return mid
    return candidates[0] if candidates else None
