"""RMB (local chat host) paths + JSON state under ~/.remedy/rmb/."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
# Dedicated port — vision stays on 8740; chat RMB does not share mmproj.
DEFAULT_CHAT_PORT = 8787

# Product defaults for agent coding + tools on 12GB class GPUs.
DEFAULT_CTX = 8192
DEFAULT_N_GPU_LAYERS = -1  # all layers when CUDA runtime available
DEFAULT_THREADS = 0  # llama-server default
DEFAULT_PARALLEL = 1
DEFAULT_CHAT_FORMAT = ""  # auto from model; optional override


def rmb_home(home_dir: str | Path | None = None) -> Path:
    if home_dir:
        root = Path(home_dir)
    else:
        try:
            from remedy.interfaces.config import get_home_dir

            root = Path(get_home_dir())
        except Exception:
            root = Path.home() / ".remedy"
    d = root / "rmb"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rmb_json_path(home_dir: str | Path | None = None) -> Path:
    return rmb_home(home_dir) / "rmb.json"


def models_dir(home_dir: str | Path | None = None) -> Path:
    d = rmb_home(home_dir) / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_rmb_json(home_dir: str | Path | None = None) -> dict[str, Any]:
    path = rmb_json_path(home_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("rmb.json read failed", exc_info=True)
        return {}


def save_rmb_json(state: dict[str, Any], home_dir: str | Path | None = None) -> None:
    path = rmb_json_path(home_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, state)


def default_state() -> dict[str, Any]:
    return {
        "enabled": False,
        # Off by default — user starts RMB explicitly (Settings / Use as provider).
        # Serve must not load a GGUF host just because Remedy API came up.
        "auto_start": False,
        "host": DEFAULT_HOST,
        "port": DEFAULT_CHAT_PORT,
        "base_url": f"http://{DEFAULT_HOST}:{DEFAULT_CHAT_PORT}/v1",
        "model_id": "qwen25-coder-7b",
        "model_path": "",
        "runtime_binary": "",
        "runtime_id": "",  # host catalog id (win-*/linux-*) or external
        "n_gpu_layers": DEFAULT_N_GPU_LAYERS,
        "ctx_size": DEFAULT_CTX,
        "threads": DEFAULT_THREADS,
        "parallel": DEFAULT_PARALLEL,
        "flash_attn": True,
        "chat_template": "",  # optional path or empty
        # --- inference engine knobs (llama-server) ---
        "temperature": 0.8,
        "top_p": 0.95,
        "top_k": 40,
        "min_p": 0.05,
        "repeat_penalty": 1.1,
        "repeat_last_n": 64,
        "seed": -1,  # -1 = random (flag omitted)
        "batch_size": 2048,
        "ubatch_size": 512,
        "mmproj": "",  # multimodal projector GGUF (vision in chat)
        "use_jinja": True,  # --jinja (use GGUF-embedded chat template)
        "rope_freq_scale": 0.0,  # 0 = llama.cpp default
        "rope_freq_base": 0.0,  # 0 = llama.cpp default
        # --- KoboldCpp-class parity knobs ('' / 0 / None = llama.cpp default) ---
        "typical_p": 0.0,  # --typical (0 = off)
        "tfs_z": 0.0,  # --tfs (0 = off)
        "mirostat": 0,  # 0 off | 1 v1 | 2 v2 (--mirostat)
        "mirostat_tau": 0.0,
        "mirostat_eta": 0.0,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "main_gpu": 0,  # --main-gpu (multi-GPU)
        "threads_batch": 0,  # --threads-batch (0 = default)
        "tensor_split": "",  # e.g. "0,512" (--tensor-split)
        "samplers": "",  # e.g. "top_k;top_p;min_p;temp" (--samplers)
        "rope_scaling": "",  # '' | linear | yarn (--rope-scaling)
        "yarn_orig_ctx": 0,  # --yarn-orig-ctx
        "yarn_factor": 0.0,  # --yarn-factor
        "yarn_beta_fast": 0.0,  # --yarn-beta-fast
        "yarn_beta_slow": 0.0,  # --yarn-beta-slow
        "no_kv_offload": False,  # --no-kv-offload
        "mlock": False,
        "no_mmap": False,
        "cache_type": "",  # '' | q8_0 | f16 | bf16 (--cache-type-k/v)
        # --- DRY + XTC samplers (KoboldCpp parity) ---
        "dry_multiplier": 0.0,  # --dry-multiplier (0 = off)
        "dry_base": 1.75,  # --dry-base
        "dry_allowed_length": 2,  # --dry-allowed-length
        "dry_penalty_last_n": -1,  # --dry-penalty-last-n (-1 = all tokens)
        "xtc_probability": 0.0,  # --xtc-probability (0 = off)
        "xtc_threshold": 0.1,  # --xtc-threshold
        "cache_reuse": 256,  # --cache-reuse (prefix cache for ReAct tool loops)
        "profile": "autofit",  # autofit | agent | turbo | quality
        "autofit": True,
        "autofit_locked": False,
        "last_autofit": None,
        "last_good_fit": None,
        "pid": None,
        # Set True while RMB owns GPU; cleared on stop / failed start
        "vision_suspended": False,
        # Persist user Stop so API recycle / watchdog does not auto-wake.
        "user_stopped": False,
    }


def merge_state(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    base = default_state()
    if existing:
        base.update({k: v for k, v in existing.items() if v is not None})
    host = str(base.get("host") or DEFAULT_HOST)
    port = int(base.get("port") or DEFAULT_CHAT_PORT)
    base["base_url"] = f"http://{host}:{port}/v1"
    return base
