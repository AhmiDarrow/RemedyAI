"""Façade used by agent, API, and tools."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from remedy.vision import progress as prog
from remedy.vision.capabilities import resolve_supports_vision
from remedy.vision.catalog import DEFAULT_MODEL_ID, catalog_public, get_model_spec
from remedy.vision.config import (
    load_vision_json,
    vision_section_from_config,
)
from remedy.vision.decoder import decode_images
from remedy.vision.health import system_health
from remedy.vision.install import (
    cancel_install as _cancel_install,
)
from remedy.vision.install import is_installed
from remedy.vision.install import reinstall_runtime as _reinstall_runtime
from remedy.vision.install import start_install as _start_install
from remedy.vision.install import uninstall as _uninstall
from remedy.vision.runtime import is_running, start_server, stop_server

logger = logging.getLogger(__name__)

# Session-level cache: sha256 or path+mtime → brief text
_decode_cache: dict[str, str] = {}


def _home_from_cfg(cfg: dict[str, Any] | None) -> Path | None:
    if not cfg:
        return None
    if cfg.get("home_dir"):
        return Path(str(cfg["home_dir"])).expanduser()
    return None


def get_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public status for Settings / API."""
    home = _home_from_cfg(cfg)
    vcfg = vision_section_from_config(cfg)
    side = load_vision_json(home)
    mid = str(side.get("model_id") or vcfg.get("model_id") or DEFAULT_MODEL_ID)
    try:
        spec = get_model_spec(mid)
        model_public = spec.to_public_dict()
    except KeyError:
        model_public = {"id": mid, "name": mid}

    installed = is_installed(mid, home)
    running = is_running(home) if installed else False
    progress = prog.snapshot()
    enabled = bool(vcfg.get("enabled") or side.get("enabled"))

    # "ready" for decode means files present + enabled; server may auto-start
    decode_ready = bool(enabled and installed)
    rid = str(
        side.get("runtime_id")
        or vcfg.get("runtime_id")
        or "win-cpu-x64"
    )
    health = system_health(model_id=mid, runtime_id=rid, home_dir=home)

    return {
        "enabled": enabled,
        "installed": installed,
        "running": running,
        "ready": decode_ready,
        "force_decode": bool(vcfg.get("force_decode")),
        "model_id": mid,
        "model": model_public,
        "backend": side.get("backend") or "llama_server",
        "base_url": side.get("base_url") or vcfg.get("base_url"),
        "port": side.get("port") or vcfg.get("port"),
        "host": side.get("host") or vcfg.get("host"),
        "runtime_version": side.get("runtime_version"),
        "runtime_id": rid,
        "progress": progress,
        "health": health,
        "warnings": list(health.get("warnings") or []),
        "catalog": catalog_public(),
        "not_ready_hint": (
            None
            if decode_ready
            else (
                "Visual decoder not installed. Enable it in Settings to download "
                f"{model_public.get('name', 'Qwen2.5-VL 3B')} (llama.cpp) for local image understanding."
            )
        ),
    }


def start_install(
    *,
    cfg: dict[str, Any] | None = None,
    model_id: str | None = None,
    runtime_id: str | None = None,
    prefer_cuda: bool = False,
) -> dict[str, Any]:
    home = _home_from_cfg(cfg)
    vcfg = vision_section_from_config(cfg)
    mid = model_id or vcfg.get("model_id") or DEFAULT_MODEL_ID
    rid = runtime_id or vcfg.get("runtime_id")
    if prefer_cuda and not rid:
        rid = "win-cuda-12.4-x64"
    # Preflight warnings (still allow install)
    health = system_health(model_id=mid, runtime_id=rid, home_dir=home)
    result = _start_install(
        model_id=mid,
        runtime_id=rid,
        home_dir=home,
        enable=True,
        prefer_cuda=prefer_cuda,
    )
    result["health"] = health
    result["warnings"] = list(health.get("warnings") or [])
    return result


def cancel_install() -> dict[str, Any]:
    return _cancel_install()


def reinstall_runtime(
    *,
    cfg: dict[str, Any] | None = None,
    prefer_cuda: bool = True,
) -> dict[str, Any]:
    """Switch llama-server binary (CPU ↔ CUDA); keeps GGUF models when present."""
    home = _home_from_cfg(cfg)
    vcfg = vision_section_from_config(cfg)
    mid = vcfg.get("model_id") or DEFAULT_MODEL_ID
    result = _reinstall_runtime(
        prefer_cuda=prefer_cuda,
        home_dir=home,
        enable=True,
        model_id=mid,
    )
    # Persist runtime_id for status
    try:
        from remedy.interfaces.api_support import _find_config_path, _write_config

        path = _find_config_path()
        if path is not None and cfg is not None:
            vision = dict(cfg.get("vision") or {}) if isinstance(cfg.get("vision"), dict) else {}
            vision["enabled"] = True
            vision["runtime_id"] = (
                "win-cuda-12.4-x64" if prefer_cuda else "win-cpu-x64"
            )
            cfg["vision"] = vision
            _write_config(path, cfg)
    except Exception:
        pass
    rid = "win-cuda-12.4-x64" if prefer_cuda else "win-cpu-x64"
    health = system_health(model_id=mid, runtime_id=rid, home_dir=home)
    result["health"] = health
    result["warnings"] = list(health.get("warnings") or [])
    return result


def uninstall(
    *,
    cfg: dict[str, Any] | None = None,
    keep_models: bool = False,
) -> dict[str, Any]:
    home = _home_from_cfg(cfg)
    return _uninstall(home_dir=home, keep_models=keep_models)


def ensure_server(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    home = _home_from_cfg(cfg)
    vcfg = vision_section_from_config(cfg)
    return start_server(home_dir=home, n_gpu_layers=int(vcfg.get("n_gpu_layers", -1)))


def stop(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return stop_server(home_dir=_home_from_cfg(cfg))


def _cache_key(path: Path) -> str:
    try:
        st = path.stat()
        return f"{path.resolve()}::{st.st_mtime_ns}::{st.st_size}"
    except OSError:
        return str(path)


def decode_for_turn(
    attachments: list[dict[str, Any]] | None,
    *,
    provider: str | None,
    model: str | None,
    cfg: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Decide native vs decode and optionally produce text briefs for images.

    Returns::

        {
          "mode": "native" | "decode" | "text_only" | "unavailable",
          "briefs": [str, ...],
          "combined": str,
          "events": [str, ...],  # process-trace lines
          "hint": str | None,
        }
    """
    atts = list(attachments or [])
    images = []
    for a in atts:
        if a.get("is_image") or str(a.get("mime") or "").startswith("image/"):
            p = Path(str(a.get("path") or ""))
            if p.is_file():
                images.append(p)

    if not images:
        return {
            "mode": "text_only",
            "briefs": [],
            "combined": "",
            "events": [],
            "hint": None,
        }

    vcfg = vision_section_from_config(cfg)
    prefer_local = bool(force or vcfg.get("force_decode"))
    # Capability of the *chat* model ignoring force_decode (still honors per-model map).
    cfg_no_force = dict(cfg or {})
    v_no = dict(vcfg)
    v_no["force_decode"] = False
    cfg_no_force["vision"] = v_no
    chat_has_vision = resolve_supports_vision(provider, model, config=cfg_no_force)

    status = get_status(cfg)
    decoder_ready = bool(status.get("ready"))

    # Prefer local decoder when asked — but never leave a vision-capable chat
    # model blind if the decoder is missing/disabled.
    if prefer_local and decoder_ready:
        pass  # fall through to decode
    elif chat_has_vision and not prefer_local:
        return {
            "mode": "native",
            "briefs": [],
            "combined": "",
            "events": [],
            "hint": None,
        }
    elif prefer_local and not decoder_ready and chat_has_vision:
        return {
            "mode": "native",
            "briefs": [],
            "combined": "",
            "events": [
                "Prefer local decoder is on, but decoder is not ready — using provider vision"
            ],
            "hint": None,
        }
    elif not decoder_ready:
        return {
            "mode": "unavailable",
            "briefs": [],
            "combined": "",
            "events": [],
            "hint": status.get("not_ready_hint"),
        }

    home = _home_from_cfg(cfg)
    side = load_vision_json(home)
    base = str(side.get("base_url") or vcfg.get("base_url") or "")
    if not base:
        if chat_has_vision:
            return {
                "mode": "native",
                "briefs": [],
                "combined": "",
                "events": ["Vision decoder base_url missing — using provider vision"],
                "hint": None,
            }
        return {
            "mode": "unavailable",
            "briefs": [],
            "combined": "",
            "events": [],
            "hint": "Vision decoder base_url missing",
        }

    if not is_running(home):
        started = start_server(
            home_dir=home,
            n_gpu_layers=int(vcfg.get("n_gpu_layers", -1)),
        )
        if not started.get("ok"):
            if chat_has_vision:
                return {
                    "mode": "native",
                    "briefs": [],
                    "combined": "",
                    "events": [
                        f"Vision start failed ({started.get('error')}) — using provider vision"
                    ],
                    "hint": None,
                }
            return {
                "mode": "unavailable",
                "briefs": [],
                "combined": "",
                "events": [f"Vision start failed: {started.get('error')}"],
                "hint": started.get("error"),
            }

    briefs: list[str] = []
    events: list[str] = []
    paths_to_decode: list[Path] = []
    for p in images:
        key = _cache_key(p)
        if key in _decode_cache:
            briefs.append(_decode_cache[key])
            events.append(f"Visual decode (cached): {p.name}")
        else:
            paths_to_decode.append(p)

    if paths_to_decode:
        t0 = time.time()
        results = decode_images(
            paths_to_decode,
            base_url=base,
            timeout_s=float(vcfg.get("timeout_s") or 90),
            max_image_bytes=int(vcfg.get("max_image_bytes") or 4 * 1024 * 1024),
        )
        for p, r in zip(paths_to_decode, results, strict=False):
            elapsed = time.time() - t0
            if r.get("ok") and r.get("text"):
                text = str(r["text"])
                _decode_cache[_cache_key(p)] = text
                briefs.append(text)
                events.append(f"Visual decode: {p.name} ({elapsed:.1f}s)")
            else:
                err = r.get("error") or "decode failed"
                events.append(f"Visual decode failed: {p.name} — {err}")
                briefs.append(
                    f"### Visual decode: {p.name}\n- **Error:** {err}\n"
                )

    combined = "\n\n".join(b for b in briefs if b).strip()
    return {
        "mode": "decode",
        "briefs": briefs,
        "combined": combined,
        "events": events,
        "hint": None,
    }


def clear_decode_cache() -> None:
    _decode_cache.clear()
