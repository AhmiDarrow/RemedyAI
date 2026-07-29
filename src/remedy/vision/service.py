"""Façade used by agent, API, and tools."""

from __future__ import annotations

import logging
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.vision import progress as prog
from remedy.vision.capabilities import resolve_supports_vision
from remedy.vision.catalog import DEFAULT_MODEL_ID, catalog_public, get_model_spec
from remedy.vision.config import (
    load_vision_json,
    save_vision_json,
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
# Host resource snapshot changes rarely; cache so Settings GETs stay snappy.
_health_cache: dict[str, Any] = {"ts": 0.0, "key": "", "value": None}
_HEALTH_CACHE_TTL_S = 30.0
# Soft-activate / idle-check throttles (status is polled often by desktop)
_activate_attempt: dict[str, float] = {"ts": 0.0, "ok": False}
_ACTIVATE_RETRY_S = 60.0
_idle_check_ts: float = 0.0
_IDLE_CHECK_MIN_S = 15.0


def _home_from_cfg(cfg: dict[str, Any] | None) -> Path | None:
    if not cfg:
        return None
    if cfg.get("home_dir"):
        return Path(str(cfg["home_dir"])).expanduser()
    return None


def _cached_system_health(
    *,
    model_id: str,
    runtime_id: str,
    home_dir: Path | None,
) -> dict[str, Any]:
    key = f"{model_id}|{runtime_id}|{home_dir}"
    now = time.time()
    if (
        _health_cache.get("key") == key
        and _health_cache.get("value") is not None
        and (now - float(_health_cache.get("ts") or 0)) < _HEALTH_CACHE_TTL_S
    ):
        return dict(_health_cache["value"])  # type: ignore[arg-type]
    health = system_health(model_id=model_id, runtime_id=runtime_id, home_dir=home_dir)
    _health_cache["ts"] = now
    _health_cache["key"] = key
    _health_cache["value"] = health
    return health


def get_status(
    cfg: dict[str, Any] | None = None,
    *,
    light: bool = False,
) -> dict[str, Any]:
    """Public status for Settings / API.

    ``light=True`` skips catalog + host health (for embedding in GET /settings).
    Running probe is always cheap (port/cache — never multi-second urlopen).
    """
    t0 = time.perf_counter()
    home = _home_from_cfg(cfg)
    vcfg = vision_section_from_config(cfg)
    side = load_vision_json(home)
    mid = str(side.get("model_id") or vcfg.get("model_id") or DEFAULT_MODEL_ID)
    # Drop retired local pins (e.g. smolvlm2-2.2b) → product SmolVLM2.
    try:
        get_model_spec(mid)
    except KeyError:
        mid = DEFAULT_MODEL_ID
        if isinstance(side, dict) and side.get("model_id"):
            side = dict(side)
            side["model_id"] = mid
            side.pop("model_path", None)
            side.pop("mmproj_path", None)
            with suppress(Exception):
                save_vision_json(side, home)
    try:
        spec = get_model_spec(mid)
        model_public = spec.to_public_dict()
    except KeyError:
        model_public = {"id": mid, "name": mid}

    # Soft-activate existing files at most once per minute (status is polled often)
    need_activate = not is_installed(mid, home) or not side.get("model_path")
    now = time.time()
    if need_activate and (now - float(_activate_attempt.get("ts") or 0)) >= _ACTIVATE_RETRY_S:
        _activate_attempt["ts"] = now
        try:
            from remedy.runtime.bundle import activate_local_bundle

            act = activate_local_bundle(home, enabled=bool(vcfg.get("enabled", True)))
            _activate_attempt["ok"] = bool(act.get("ok"))
            if act.get("ok"):
                side = load_vision_json(home)
                mid = str(side.get("model_id") or mid)
        except Exception:
            _activate_attempt["ok"] = False

    installed = is_installed(mid, home)
    running = is_running(home) if installed else False
    # Idle stop: background watcher handles it; only sample here every N seconds
    global _idle_check_ts
    if running and (now - _idle_check_ts) >= _IDLE_CHECK_MIN_S:
        _idle_check_ts = now
        with suppress(Exception):
            from remedy.vision.runtime import ensure_idle_watcher, maybe_idle_stop

            ensure_idle_watcher(home)
            idle_res = maybe_idle_stop(
                home, idle_stop_s=int(vcfg.get("idle_stop_s") or 600)
            )
            if idle_res.get("stopped"):
                running = False
    elif running:
        with suppress(Exception):
            from remedy.vision.runtime import ensure_idle_watcher

            ensure_idle_watcher(home)
    progress = prog.snapshot()
    enabled = bool(vcfg.get("enabled") or side.get("enabled"))

    # "ready" for decode means files present + enabled; server may auto-start
    decode_ready = bool(enabled and installed)
    rid = str(
        side.get("runtime_id")
        or vcfg.get("runtime_id")
        or "win-cpu-x64"
    )

    out: dict[str, Any] = {
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
        "not_ready_hint": (
            None
            if decode_ready
            else (
                "Local model is off. Enable Vision & nano swarm in Settings — "
                f"{model_public.get('name', 'SmolVLM2 2.2B')} starts with Remedy when ready."
                if installed
                else (
                    "Local model not installed yet. Open Settings → Vision & nano swarm "
                    f"to download pinned {model_public.get('name', 'SmolVLM2 2.2B')} "
                    "(one-time; then starts with Remedy)."
                )
            )
        ),
        "bundled": bool(side.get("bundled")),
        "local_roles": ["vision", "nano", "helper"],
        "bundle_policy": "cpu_and_cuda",
        "auto_start": bool(vcfg.get("auto_start", True)),
        "delivery": "first_run_download",
    }

    if not light:
        health = _cached_system_health(model_id=mid, runtime_id=rid, home_dir=home)
        out["health"] = health
        out["warnings"] = list(health.get("warnings") or [])
        out["catalog"] = catalog_public()
    else:
        out["health"] = None
        out["warnings"] = []
        out["catalog"] = None

    ms = (time.perf_counter() - t0) * 1000
    if ms > 100:
        logger.warning(
            "vision get_status slow light=%s installed=%s running=%s (%.0fms)",
            light,
            installed,
            running,
            ms,
        )
    else:
        logger.debug(
            "vision get_status light=%s installed=%s running=%s (%.0fms)",
            light,
            installed,
            running,
            ms,
        )
    return out


def activate_bundle(
    *,
    cfg: dict[str, Any] | None = None,
    enabled: bool = True,
    start: bool = True,
) -> dict[str, Any]:
    """Point vision.json at existing files (user install or optional product bundle)."""
    home = _home_from_cfg(cfg)
    from remedy.runtime.bundle import activate_local_bundle, bundle_available

    result = activate_local_bundle(home, enabled=enabled)
    status = get_status(cfg, light=True)
    out = {**status}
    out["ok"] = bool(result.get("ok"))
    if result.get("error"):
        out["error"] = result["error"]
    if result.get("state"):
        out["state"] = result["state"]
    out["mode"] = "local_files" if result.get("ok") else "missing"
    if not result.get("ok"):
        out["diagnostic"] = result.get("diagnostic") or bundle_available()
    else:
        out["message"] = "Local model files activated."
        if start and enabled:
            started = ensure_server(cfg)
            out["server"] = started
            if started.get("ok"):
                out["message"] = "Local model activated and server started."
            out["running"] = bool(started.get("ok"))
    return out


def maybe_autostart_local_model(
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """If installed + enabled + auto_start, start llama-server (starts with Remedy)."""
    home = _home_from_cfg(cfg)
    vcfg = vision_section_from_config(cfg)
    mid = str(vcfg.get("model_id") or DEFAULT_MODEL_ID)
    if not bool(vcfg.get("enabled", True)):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if not bool(vcfg.get("auto_start", True)):
        return {"ok": False, "skipped": True, "reason": "auto_start_off"}
    if not is_installed(mid, home):
        # Soft-activate user files if present (no download)
        try:
            from remedy.runtime.bundle import activate_local_bundle

            act = activate_local_bundle(home, enabled=True)
            if not act.get("ok") and not is_installed(mid, home):
                return {"ok": False, "skipped": True, "reason": "not_installed"}
        except Exception:
            return {"ok": False, "skipped": True, "reason": "not_installed"}
    try:
        return ensure_server(cfg)
    except Exception as e:
        logger.warning("maybe_autostart_local_model failed: %s", e)
        return {"ok": False, "error": str(e)}


def start_install(
    *,
    cfg: dict[str, Any] | None = None,
    model_id: str | None = None,
    runtime_id: str | None = None,
    prefer_cuda: bool = False,
) -> dict[str, Any]:
    """Install pinned SmolVLM2 (first-run download) or activate existing files.

    Primary path for new PCs: network download of catalog-pinned assets.
    If files already present: activate + start server (no re-download).
    """
    home = _home_from_cfg(cfg)
    vcfg = vision_section_from_config(cfg)
    mid = model_id or vcfg.get("model_id") or DEFAULT_MODEL_ID
    try:
        get_model_spec(mid)
    except KeyError:
        mid = DEFAULT_MODEL_ID
    rid = runtime_id or vcfg.get("runtime_id")
    if prefer_cuda and not rid:
        rid = "win-cuda-12.4-x64"

    # Already on disk (previous download or rare product bundle) — activate + start
    if is_installed(mid, home):
        act = activate_bundle(cfg=cfg, enabled=True, start=True)
        act["mode"] = "already_installed"
        act["message"] = act.get("message") or (
            "Local model already present — activated and starting with Remedy."
        )
        return act

    act = activate_bundle(cfg=cfg, enabled=True, start=True)
    if act.get("ok") and is_installed(mid, home):
        act["mode"] = "already_installed"
        return act

    # First-run / recovery: download the same pinned catalog model
    health = system_health(model_id=mid, runtime_id=rid, home_dir=home)
    if prefer_cuda is False and not rid:
        # Prefer CUDA when host has NVIDIA (option B runtime pick, same weights)
        if health.get("nvidia_detected"):
            prefer_cuda = True
            rid = "win-cuda-12.4-x64"
    result = _start_install(
        model_id=mid,
        runtime_id=rid,
        home_dir=home,
        enable=True,
        prefer_cuda=prefer_cuda,
    )
    result["health"] = health
    result["warnings"] = list(health.get("warnings") or [])
    result["mode"] = "download"
    result["message"] = (
        "Downloading pinned SmolVLM2 2.2B + llama-server (same files on every PC). "
        "Server starts automatically when install finishes."
    )
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
    # Wake from idle: starting counts as use
    result = start_server(home_dir=home, n_gpu_layers=int(vcfg.get("n_gpu_layers", -1)))
    return result


def stop(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return stop_server(home_dir=_home_from_cfg(cfg))


def _decode_images_queued(
    paths: list[Path],
    *,
    base_url: str,
    timeout_s: float,
    max_image_bytes: int,
) -> list[dict[str, Any]]:
    """Run each decode as a LocalJob so nano/vision share one llama-server."""
    try:
        from remedy.runtime.jobs import LocalJob, default_queue
        from remedy.runtime.local_infer import ensure_handlers_registered
        from remedy.runtime.roles import LocalRole

        ensure_handlers_registered()
        q = default_queue()
        results: list[dict[str, Any]] = []
        for p in paths:
            job = LocalJob(
                role=LocalRole.VISION,
                kind="vision_decode",
                payload={
                    "path": str(p),
                    "base_url": base_url,
                    "timeout_s": timeout_s,
                    "max_image_bytes": max_image_bytes,
                },
                priority=10,  # vision slightly ahead of nano classify
            )
            out = q.submit(job, wait=True, timeout=float(timeout_s) + 30)
            if out.get("ok") and isinstance(out.get("result"), dict):
                results.append(out["result"])
            else:
                results.append(
                    {
                        "ok": False,
                        "path": str(p),
                        "error": out.get("error") or "queue failed",
                        "text": "",
                    }
                )
        return results
    except Exception as e:
        logger.warning("Queued decode failed (%s); falling back direct", e)
        return decode_images(
            paths,
            base_url=base_url,
            timeout_s=timeout_s,
            max_image_bytes=max_image_bytes,
        )


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
        # Serialize through shared job queue (vision | nano exclusive on one server)
        results = _decode_images_queued(
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
