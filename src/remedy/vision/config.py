"""Vision config + vision.json side state under ~/.remedy/vision/."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from remedy.vision.catalog import (
    DEFAULT_HOST,
    DEFAULT_MODEL_ID,
    DEFAULT_PORT,
    default_runtime_id,
)


def remedy_home(home_dir: str | Path | None = None) -> Path:
    if home_dir:
        return Path(home_dir).expanduser()
    env = os.environ.get("REMEDY_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".remedy"


def vision_root(home_dir: str | Path | None = None) -> Path:
    return remedy_home(home_dir) / "vision"


def vision_json_path(home_dir: str | Path | None = None) -> Path:
    return vision_root(home_dir) / "vision.json"


def models_dir(model_id: str, home_dir: str | Path | None = None) -> Path:
    return vision_root(home_dir) / "models" / model_id


def runtime_dir(home_dir: str | Path | None = None) -> Path:
    return vision_root(home_dir) / "runtime"


def downloads_dir(home_dir: str | Path | None = None) -> Path:
    return vision_root(home_dir) / "downloads"


def load_vision_json(home_dir: str | Path | None = None) -> dict[str, Any]:
    path = vision_json_path(home_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_vision_json(data: dict[str, Any], home_dir: str | Path | None = None) -> Path:
    root = vision_root(home_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = vision_json_path(home_dir)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Force runtime hot-path reparse (Windows mtime granularity)
    try:
        from remedy.vision.runtime import invalidate_running_cache

        invalidate_running_cache()
    except Exception:
        pass
    return path


def vision_section_from_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize [vision] table from loaded config dict."""
    raw = (cfg or {}).get("vision")
    if not isinstance(raw, dict):
        raw = {}
    host = str(raw.get("host") or DEFAULT_HOST).strip() or DEFAULT_HOST
    try:
        port = int(raw.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    model_id = str(raw.get("model_id") or DEFAULT_MODEL_ID).strip() or DEFAULT_MODEL_ID
    # Migrate retired local pins → SmolVLM2 (product ships a single local VLM).
    _LEGACY_LOCAL_MODELS = {
        "qwen2.5-vl-3b",
        "qwen2.5-vl",
        "qwen2.5vl-3b",
        "qwen-vl-3b",
        "qwen2-vl",
        "qwen-vl",
    }
    if model_id.lower() in _LEGACY_LOCAL_MODELS:
        model_id = DEFAULT_MODEL_ID
    base_url = str(raw.get("base_url") or f"http://{host}:{port}/v1").strip()
    return {
        # Bundled local model: on by default when [vision] present without explicit flag
        "enabled": bool(raw.get("enabled", True)),
        "model_id": model_id,
        "host": host,
        "port": port,
        "base_url": base_url,
        "auto_start": bool(raw.get("auto_start", True)),
        "idle_stop_s": int(raw.get("idle_stop_s") or 600),
        "max_image_bytes": int(raw.get("max_image_bytes") or 4 * 1024 * 1024),
        "timeout_s": float(raw.get("timeout_s") or 90),
        "n_gpu_layers": int(raw.get("n_gpu_layers", -1)),
        "runtime_id": str(raw.get("runtime_id") or default_runtime_id()),
        "force_decode": bool(raw.get("force_decode", False)),
        "force_native": bool(raw.get("force_native", False)),
    }


def default_vision_toml_block() -> str:
    return f"""
# Local model (SmolVLM2 2.2B, Apache 2.0) — vision + nano swarm (+ helper)
# First-run download of pinned files (not in installer). Same model on every PC.
# auto_start: llama-server starts with Remedy once installed.
[vision]
enabled = true
model_id = "{DEFAULT_MODEL_ID}"
host = "{DEFAULT_HOST}"
port = {DEFAULT_PORT}
auto_start = true
idle_stop_s = 600
"""
