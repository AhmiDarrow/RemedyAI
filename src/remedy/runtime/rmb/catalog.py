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
        }


# Default product model for agent coding on 12GB: Qwen2.5-Coder 7B Q4_K_M
RMB_MODELS: dict[str, RmbModelSpec] = {
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

DEFAULT_RMB_MODEL_ID = "qwen25-coder-7b"

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

    size_m = re.search(r"(?:^|[^0-9])(7b|14b|32b|72b)(?:[^0-9]|$)", h, re.I)
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
