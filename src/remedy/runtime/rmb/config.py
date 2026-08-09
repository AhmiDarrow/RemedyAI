"""RMB (local chat host) paths + JSON state under ~/.remedy/rmb/."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


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
        "runtime_id": "",  # win-cuda-12.4-x64 | win-cpu-x64 | external
        "n_gpu_layers": DEFAULT_N_GPU_LAYERS,
        "ctx_size": DEFAULT_CTX,
        "threads": DEFAULT_THREADS,
        "parallel": DEFAULT_PARALLEL,
        "flash_attn": True,
        "chat_template": "",  # optional path or empty
        "profile": "agent",  # agent | turbo | quality
        "pid": None,
        # Set True while RMB owns GPU; cleared on stop / failed start
        "vision_suspended": False,
    }


def merge_state(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    base = default_state()
    if existing:
        base.update({k: v for k, v in existing.items() if v is not None})
    host = str(base.get("host") or DEFAULT_HOST)
    port = int(base.get("port") or DEFAULT_CHAT_PORT)
    base["base_url"] = f"http://{host}:{port}/v1"
    return base
