"""Manage the RMB local chat llama-server (coding + tools).

Separate from vision (port 8740 + mmproj). Brand: RMB — engine: llama-server.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request

from remedy.runtime.rmb.catalog import (
    DEFAULT_RMB_MODEL_ID,
    RMB_PROFILES,
    catalog_public,
    get_model_spec,
)
from remedy.runtime.rmb.config import (
    DEFAULT_CHAT_PORT,
    DEFAULT_HOST,
    load_rmb_json,
    merge_state,
    models_dir,
    save_rmb_json,
)

logger = logging.getLogger(__name__)

_proc: subprocess.Popen[Any] | None = None
_last_used: float = 0.0
_lock = threading.Lock()
_atexit_registered = False
_starting_until: float = 0.0
_user_stopped: bool = False

_running_cache: dict[str, Any] = {"ts": 0.0, "value": False, "key": ""}
_RUNNING_CACHE_TTL_S = 2.0
_HEALTH_TIMEOUT_S = 0.4


def _port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health(base_url: str, timeout: float = _HEALTH_TIMEOUT_S) -> bool:
    from remedy.core.security import is_loopback_service_url, urlopen_no_redirect

    base = (base_url or "").rstrip("/")
    if not base or not is_loopback_service_url(base):
        return False
    for path in ("/models", "/v1/models"):
        url = base if base.endswith(path) else base.rstrip("/") + path
        if path == "/v1/models" and base.endswith("/v1"):
            url = base + "/models"
        elif path == "/models" and base.endswith("/v1"):
            url = base + "/models"
        try:
            req = Request(url, headers={"User-Agent": "RemedyAI-RMB/1.0"})
            with urlopen_no_redirect(req, timeout=timeout) as resp:  # type: ignore[union-attr]
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except Exception:
            continue
    return False


def mark_used() -> None:
    global _last_used
    _last_used = time.time()


def managed_process_alive() -> bool:
    """True when this process holds a live child llama-server."""
    proc = _proc
    return proc is not None and proc.poll() is None


def is_starting() -> bool:
    """True during spawn → healthy window (blocks vision heal/race)."""
    if managed_process_alive() and not is_running(force=True, require_http=True):
        return True
    return time.time() < float(_starting_until or 0)


def is_running(
    home_dir: str | Path | None = None,
    *,
    force: bool = False,
    require_http: bool = False,
) -> bool:
    state = merge_state(load_rmb_json(home_dir))
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_CHAT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    key = f"{host}:{port}:{'h' if require_http else 'p'}"
    now = time.time()
    if (
        not force
        and _running_cache.get("key") == key
        and (now - float(_running_cache.get("ts") or 0)) < _RUNNING_CACHE_TTL_S
    ):
        return bool(_running_cache.get("value"))

    proc = _proc
    child = proc is not None and proc.poll() is None
    port_up = _port_open(host, port)
    if child and port_up:
        ok = True if not require_http else _health(base)
    elif port_up:
        # Prefer HTTP health; bare open port is not enough (exclusive-host safety)
        healthy = _health(base)
        ok = healthy if require_http else (healthy or child)
    else:
        ok = False
    _running_cache["ts"] = now
    _running_cache["value"] = ok
    _running_cache["key"] = key
    return ok


def invalidate_cache() -> None:
    _running_cache["ts"] = 0.0
    _running_cache["value"] = False


def _looks_like_llama_server(pid: int) -> bool:
    """Avoid killing unrelated processes when rmb.json pid is stale."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            text = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
            return "llama-server" in text or "llama_server" in text
        except OSError:
            return True
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue).ProcessName",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creationflags,
        )
        name = (out.stdout or "").strip().lower()
        return "llama" in name or name in ("llama-server", "llama_server")
    except Exception:
        return False


def _model_search_roots(home_dir: str | Path | None) -> list[Path]:
    roots = [
        models_dir(home_dir),
        Path.home() / ".remedy" / "models",
        Path.home() / ".remedy" / "rmb" / "models",
    ]
    # Optional extra dirs via env (semicolon-separated on Windows)
    extra = (os.environ.get("REMEDY_RMB_MODEL_DIRS") or "").strip()
    if extra:
        for part in extra.replace(";", os.pathsep).split(os.pathsep):
            p = Path(part.strip())
            if p.is_dir():
                roots.append(p)
    return roots


def _find_llama_binary(state: dict[str, Any], home_dir: str | Path | None) -> Path | None:
    rb = state.get("runtime_binary")
    if rb and Path(str(rb)).is_file():
        return Path(str(rb))
    try:
        from remedy.vision.install import runtime_binary_path

        p = runtime_binary_path(home_dir)
        if p and Path(p).is_file():
            return Path(p)
    except Exception:
        pass
    try:
        from remedy.runtime.bundle import runtime_binary_from_bundle

        rid = str(state.get("runtime_id") or "")
        if rid:
            p = runtime_binary_from_bundle(rid)
            if p and Path(p).is_file():
                return Path(p)
        for rid in ("win-cuda-12.4-x64", "win-cpu-x64"):
            p = runtime_binary_from_bundle(rid)
            if p and Path(p).is_file():
                return Path(p)
    except Exception:
        pass
    # Optional env override for developer builds
    env_bin = (os.environ.get("REMEDY_LLAMA_SERVER") or "").strip()
    if env_bin and Path(env_bin).is_file():
        return Path(env_bin)
    return None


def discover_ggufs(home_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """List GGUF files Remedy can load into RMB (any model, not catalog-only)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for root in _model_search_roots(home_dir):
        if not root.is_dir():
            continue
        try:
            for p in sorted(root.glob("*.gguf")):
                if not p.is_file():
                    continue
                key = str(p.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    size_gb = round(p.stat().st_size / (1024**3), 2)
                except OSError:
                    size_gb = 0.0
                out.append(
                    {
                        "path": str(p),
                        "name": p.name,
                        "size_gb": size_gb,
                        "dir": str(root),
                    }
                )
        except Exception:
            continue
    return out


def _resolve_model_path(state: dict[str, Any], home_dir: str | Path | None) -> Path | None:
    """Resolve any GGUF for RMB — explicit path first, then catalog, then scan."""
    mp = str(state.get("model_path") or "").strip()
    if mp and Path(mp).is_file():
        return Path(mp)

    mid = str(state.get("model_id") or DEFAULT_RMB_MODEL_ID)
    spec = get_model_spec(mid)
    size_tag = (spec.size_label or "7b").upper()  # e.g. 7B, 14B

    for root in _model_search_roots(home_dir):
        cand = root / spec.filename
        if cand.is_file():
            return cand
        if not root.is_dir():
            continue
        # Prefer exact-ish catalog tokens (size label, not hardcoded 7B)
        for p in root.glob(f"*Coder*{size_tag}*.gguf"):
            if p.is_file():
                return p
        for p in root.glob(f"*{size_tag}*Q4_K_M*.gguf"):
            if p.is_file() and ("coder" in p.name.lower() or "instruct" in p.name.lower()):
                return p
        # Normalize qwen25 → qwen2.5 style fragments
        frag = mid.replace("_", "-").lower().replace("qwen25", "qwen2.5")
        for p in root.glob("*.gguf"):
            n = p.name.lower().replace("qwen2.5", "qwen25")
            if frag and frag.replace("qwen2.5", "qwen25") in n.replace("qwen2.5", "qwen25"):
                return p

    # Single GGUF in rmb/models → use it
    try:
        rmb_models = sorted(models_dir(home_dir).glob("*.gguf"))
        if len(rmb_models) == 1 and rmb_models[0].is_file():
            return rmb_models[0]
        for p in rmb_models:
            n = p.name.lower()
            if "coder" in n:
                return p
        for p in rmb_models:
            if "instruct" in p.name.lower():
                return p
        if rmb_models:
            return rmb_models[0]
    except Exception:
        pass
    return None


def _nvidia_ok() -> bool:
    try:
        r = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        return False


def _build_cmd(
    binary: Path,
    model: Path,
    *,
    host: str,
    port: int,
    ctx: int,
    ngl: int,
    threads: int,
    parallel: int,
    flash_attn: bool,
) -> list[str]:
    cmd = [
        str(binary),
        "-m",
        str(model),
        "--host",
        host,
        "--port",
        str(port),
        "--ctx-size",
        str(max(2048, int(ctx))),
        "-ngl",
        str(int(ngl)),
        "--parallel",
        str(max(1, int(parallel))),
        "--cont-batching",
        "--jinja",
    ]
    if threads and int(threads) > 0:
        cmd.extend(["--threads", str(int(threads))])
    if flash_attn:
        cmd.extend(["-fa", "on"])
    return cmd


def _set_vision_suspended(
    home_dir: str | Path | None,
    suspended: bool,
    *,
    was_running: bool | None = None,
) -> None:
    try:
        state = merge_state(load_rmb_json(home_dir))
        state["vision_suspended"] = bool(suspended)
        if was_running is not None:
            state["vision_was_running"] = bool(was_running)
        if not suspended:
            state.pop("vision_was_running", None)
        save_rmb_json(state, home_dir)
    except Exception:
        logger.debug("RMB: failed to persist vision_suspended=%s", suspended, exc_info=True)


def _suspend_smolvlm(home_dir: str | Path | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"stopped": False, "suspended": True}
    try:
        from remedy.vision.runtime import is_running as vision_running
        from remedy.vision.runtime import stop_server as vision_stop

        if vision_running(home_dir, force=True):
            vision_stop(home_dir=home_dir)
            out["stopped"] = True
            logger.info("RMB: stopped SmolVLM/vision llama-server (exclusive host)")
    except Exception as e:
        out["error"] = str(e)
        logger.debug("RMB: vision stop failed: %s", e)
    try:
        from remedy.runtime.mdl_runtime import stop_all_tiers

        stop_all_tiers()
        out["mdl_stopped"] = True
    except Exception:
        pass
    return out


def _resume_smolvlm_if_wanted(home_dir: str | Path | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"resumed": False}
    try:
        from remedy.interfaces.api_support import load_config
        from remedy.vision.config import vision_section_from_config
        from remedy.vision.service import ensure_server
        from remedy.runtime.rmb.mode import is_local_agent_mode

        cfg = load_config() or {}
        if home_dir and isinstance(cfg, dict):
            cfg = {**cfg, "home_dir": str(home_dir)}
        vcfg = vision_section_from_config(cfg if isinstance(cfg, dict) else {})
        if is_local_agent_mode(cfg if isinstance(cfg, dict) else None):
            out["reason"] = "chat_still_rmb"
            return out
        if is_starting() or managed_process_alive() or is_running(home_dir, force=True, require_http=True):
            out["reason"] = "rmb_still_running"
            return out
        if not bool(vcfg.get("enabled", True)):
            out["reason"] = "vision_disabled"
            return out
        if not bool(vcfg.get("auto_start", True)):
            out["reason"] = "auto_start_off"
            return out
        r = ensure_server(cfg if isinstance(cfg, dict) else None)
        out["resumed"] = bool(r.get("ok"))
        out["server"] = r
    except Exception as e:
        out["error"] = str(e)
    return out


def _mark_starting(seconds: float = 180.0) -> None:
    global _starting_until
    _starting_until = time.time() + max(5.0, float(seconds))


def _clear_starting() -> None:
    global _starting_until
    _starting_until = 0.0


def wake_rmb_async(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Start RMB in a daemon thread if needed (never blocks the chat path)."""
    if is_running(home_dir, force=True, require_http=True):
        mark_used()
        return {"ok": True, "already_running": True}
    if is_starting() or managed_process_alive():
        return {"ok": True, "starting": True}
    if _user_stopped:
        return {"ok": False, "error": "RMB was stopped by user; Start RMB to load again"}

    def _run() -> None:
        try:
            start_rmb_server(home_dir=home_dir, wait_s=120.0)
        except Exception:
            logger.exception("RMB background start failed")

    t = threading.Thread(target=_run, name="remedy-rmb-wake", daemon=True)
    t.start()
    return {"ok": True, "starting": True, "async": True}


def start_rmb_server(
    *,
    home_dir: str | Path | None = None,
    wait_s: float = 90.0,
) -> dict[str, Any]:
    """Start RMB chat llama-server if not healthy.

    Unloads SmolVLM first. On failure, clears vision_suspended.
    Health wait does **not** hold the process lock so Stop can interrupt.
    """
    global _proc, _atexit_registered, _user_stopped

    # Fast path outside lock
    if is_running(home_dir, force=True, require_http=True):
        mark_used()
        _set_vision_suspended(home_dir, True)
        st0 = merge_state(load_rmb_json(home_dir))
        with contextlib.suppress(Exception):
            sync_context_window_cache(st0)
        return {
            "ok": True,
            "already_running": True,
            "base_url": st0.get("base_url"),
            "ctx_size": int(st0.get("ctx_size") or 0) or None,
            "vision_suspended": True,
        }

    with _lock:
        if is_running(home_dir, force=True, require_http=True):
            mark_used()
            _set_vision_suspended(home_dir, True)
            return {
                "ok": True,
                "already_running": True,
                "base_url": merge_state(load_rmb_json(home_dir)).get("base_url"),
                "pid": _proc.pid if managed_process_alive() else None,
                "vision_suspended": True,
            }
        if managed_process_alive():
            # Another start in progress
            _mark_starting(float(wait_s) + 30)
            return {"ok": True, "starting": True, "vision_suspended": True}

        state = merge_state(load_rmb_json(home_dir))
        vision_suspend = _suspend_smolvlm(home_dir)
        state["vision_suspended"] = True
        state["vision_was_running"] = bool(vision_suspend.get("stopped"))
        save_rmb_json(state, home_dir)
        _mark_starting(float(wait_s) + 60)
        _user_stopped = False

        def _fail(payload: dict[str, Any]) -> dict[str, Any]:
            _clear_starting()
            _set_vision_suspended(home_dir, False)
            payload = dict(payload)
            payload.setdefault("vision_suspended", False)
            payload.setdefault("vision", vision_suspend)
            return payload

        host = str(state.get("host") or DEFAULT_HOST)
        port = int(state.get("port") or DEFAULT_CHAT_PORT)
        base = f"http://{host}:{port}/v1"
        state["base_url"] = base

        binary = _find_llama_binary(state, home_dir)
        if binary is None:
            return _fail(
                {
                    "ok": False,
                    "error": (
                        "llama-server binary not found. Enable Local model (vision) once "
                        "to install the runtime, set REMEDY_LLAMA_SERVER, or set RMB runtime "
                        "path in Settings."
                    ),
                }
            )

        model = _resolve_model_path(state, home_dir)
        if model is None:
            spec = get_model_spec(str(state.get("model_id") or DEFAULT_RMB_MODEL_ID))
            found = discover_ggufs(home_dir)
            return _fail(
                {
                    "ok": False,
                    "error": (
                        f"GGUF not found for {spec.name}. Place any coding GGUF in "
                        f"{models_dir(home_dir)} or set model path in Settings → RMB."
                    ),
                    "expected_filename": spec.filename,
                    "models_dir": str(models_dir(home_dir)),
                    "discovered_ggufs": [g["name"] for g in found[:12]],
                }
            )

        # If configured port is occupied by a non-healthy service, refuse (don't hop)
        if _port_open(host, port) and not _health(base):
            return _fail(
                {
                    "ok": False,
                    "error": (
                        f"Port {port} is in use but not a healthy llama-server. "
                        "Stop the other process or change RMB port in Settings."
                    ),
                    "port": port,
                }
            )

        profile = str(state.get("profile") or "agent")
        prof = RMB_PROFILES.get(profile) or RMB_PROFILES["agent"]
        ctx = int(state.get("ctx_size") or prof.get("ctx_size") or 8192)
        ngl = int(
            state.get("n_gpu_layers")
            if state.get("n_gpu_layers") is not None
            else -1
        )
        if ngl < 0 and not _nvidia_ok():
            ngl = 0

        cmd = _build_cmd(
            binary,
            model,
            host=host,
            port=port,
            ctx=ctx,
            ngl=ngl,
            threads=int(state.get("threads") or 0),
            parallel=int(state.get("parallel") or 1),
            flash_attn=bool(state.get("flash_attn", True)),
        )

        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        env = os.environ.copy()
        cuda = os.environ.get("CUDA_PATH") or os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "cuda_toolkit"
        )
        if cuda and Path(cuda, "bin").is_dir():
            env["PATH"] = str(Path(cuda) / "bin") + os.pathsep + env.get("PATH", "")

        log_path = models_dir(home_dir).parent / "llama-server.log"
        log_f: Any = subprocess.DEVNULL
        try:
            log_f = open(log_path, "ab", buffering=0)  # noqa: SIM115
        except Exception:
            log_f = subprocess.DEVNULL

        try:
            _proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(binary.parent),
                creationflags=creation,
            )
        except Exception as e:
            if log_f is not subprocess.DEVNULL:
                with contextlib.suppress(Exception):
                    log_f.close()
            return _fail({"ok": False, "error": f"failed to spawn llama-server: {e}"})

        state["model_path"] = str(model)
        state["runtime_binary"] = str(binary)
        state["pid"] = _proc.pid
        state["enabled"] = True
        state["vision_suspended"] = True
        state["base_url"] = base
        state["port"] = port
        save_rmb_json(state, home_dir)
        invalidate_cache()

        if not _atexit_registered:
            atexit.register(
                lambda: stop_rmb_server(home_dir=home_dir, resume_vision=False)
            )
            _atexit_registered = True

        # Snapshot for wait loop outside lock
        wait_proc = _proc
        wait_pid = _proc.pid
        wait_base = base
        wait_ctx = ctx
        wait_ngl = ngl
        wait_model = str(model)
        wait_binary = str(binary)
        wait_log = str(log_path)
        wait_vision = vision_suspend
        wait_mid = state.get("model_id")

    # --- health wait OUTSIDE lock so Stop can run ---
    if wait_s <= 0:
        return {
            "ok": True,
            "starting": True,
            "base_url": wait_base,
            "pid": wait_pid,
            "vision_suspended": True,
            "vision": wait_vision,
        }

    deadline = time.time() + max(1.0, float(wait_s))
    while time.time() < deadline:
        if _user_stopped:
            return {
                "ok": False,
                "error": "RMB start cancelled (stopped)",
                "vision_suspended": False,
            }
        if wait_proc.poll() is not None:
            code = wait_proc.returncode
            with _lock:
                if _proc is wait_proc:
                    _proc = None
            _clear_starting()
            _set_vision_suspended(home_dir, False)
            if log_f is not subprocess.DEVNULL:
                with contextlib.suppress(Exception):
                    log_f.close()
            return {
                "ok": False,
                "error": f"llama-server exited early (code {code}). See {wait_log}",
                "log": wait_log,
                "vision_suspended": False,
                "vision": wait_vision,
            }
        if is_running(home_dir, force=True, require_http=True):
            mark_used()
            _clear_starting()
            with contextlib.suppress(Exception):
                from remedy.nanoswarm.token_nanobot import cache_context_window

                cache_context_window(wait_base, wait_mid, wait_ctx)
                cache_context_window(wait_base, Path(wait_model).name, wait_ctx)
            return {
                "ok": True,
                "base_url": wait_base,
                "pid": wait_pid,
                "model_path": wait_model,
                "binary": wait_binary,
                "ctx_size": wait_ctx,
                "n_gpu_layers": wait_ngl,
                "log": wait_log,
                "vision_suspended": True,
                "vision": wait_vision,
            }
        time.sleep(0.4)

    # Timeout: kill orphan
    with _lock:
        if _proc is wait_proc and wait_proc.poll() is None:
            with contextlib.suppress(Exception):
                wait_proc.terminate()
            try:
                wait_proc.wait(timeout=3)
            except Exception:
                with contextlib.suppress(Exception):
                    wait_proc.kill()
            _proc = None
    _clear_starting()
    _set_vision_suspended(home_dir, False)
    if log_f is not subprocess.DEVNULL:
        with contextlib.suppress(Exception):
            log_f.close()
    return {
        "ok": False,
        "error": f"llama-server did not become healthy within {wait_s}s",
        "log": wait_log,
        "base_url": wait_base,
        "vision_suspended": False,
        "vision": wait_vision,
    }


def stop_rmb_server(
    home_dir: str | Path | None = None,
    *,
    resume_vision: bool = True,
) -> dict[str, Any]:
    """Stop RMB chat host; clear vision suspend; optionally restore SmolVLM.

    ``resume_vision=False`` for atexit/lifespan so we never restart Smol while
    the process is exiting.
    """
    global _proc, _user_stopped
    with _lock:
        _user_stopped = True
        _clear_starting()
        killed = False
        if _proc is not None:
            with contextlib.suppress(Exception):
                _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    _proc.kill()
            _proc = None
            killed = True
        state = merge_state(load_rmb_json(home_dir))
        pid = state.get("pid")
        if pid:
            try:
                ipid = int(pid)
            except (TypeError, ValueError):
                ipid = 0
            if ipid and _looks_like_llama_server(ipid):
                if os.name == "nt":
                    with contextlib.suppress(Exception):
                        subprocess.run(
                            ["taskkill", "/PID", str(ipid), "/T", "/F"],
                            capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        killed = True
                else:
                    with contextlib.suppress(Exception):
                        os.kill(ipid, 15)
                        killed = True
            elif ipid:
                logger.warning(
                    "RMB pid %s does not look like llama-server; skipping kill",
                    ipid,
                )
        state["pid"] = None
        state["vision_suspended"] = False
        save_rmb_json(state, home_dir)
        invalidate_cache()

    vision_resume: dict[str, Any] = {"resumed": False, "skipped": True}
    if resume_vision:
        vision_resume = _resume_smolvlm_if_wanted(home_dir)
    return {
        "ok": True,
        "stopped": killed,
        "vision_suspended": False,
        "vision_resume": vision_resume,
    }


def ensure_rmb_server(
    home_dir: str | Path | None = None,
    *,
    wait_s: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Start if enabled. Does not start when disabled unless force=True."""
    state = merge_state(load_rmb_json(home_dir))
    if is_running(home_dir, force=True, require_http=True):
        mark_used()
        return {"ok": True, "already_running": True, "base_url": state.get("base_url")}
    if _user_stopped and not force:
        return {"ok": False, "error": "RMB stopped by user"}
    if not state.get("enabled") and not force:
        return {"ok": False, "error": "RMB not enabled"}
    ws = 90.0 if wait_s is None else float(wait_s)
    return start_rmb_server(home_dir=home_dir, wait_s=ws)


def get_rmb_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public status for Settings UI."""
    home = None
    if isinstance(cfg, dict) and cfg.get("home_dir"):
        home = cfg.get("home_dir")
    rmb_cfg = {}
    if isinstance(cfg, dict) and isinstance(cfg.get("rmb"), dict):
        rmb_cfg = dict(cfg["rmb"])
    state = merge_state({**load_rmb_json(home), **rmb_cfg})
    model_path = _resolve_model_path(state, home)
    binary = _find_llama_binary(state, home)
    running = is_running(home, force=True, require_http=True)
    ready = running
    starting = is_starting()

    # Auto-heal stuck suspend only when nothing is starting/alive
    if (
        bool(state.get("vision_suspended"))
        and not running
        and not starting
        and not managed_process_alive()
    ):
        try:
            from remedy.runtime.rmb.mode import is_local_agent_mode

            still_agent = is_local_agent_mode(cfg if isinstance(cfg, dict) else None)
        except Exception:
            still_agent = False
        if not still_agent:
            _set_vision_suspended(home, False)
            state["vision_suspended"] = False

    spec = get_model_spec(str(state.get("model_id") or DEFAULT_RMB_MODEL_ID))
    installed = model_path is not None and binary is not None
    discovered = discover_ggufs(home)
    local_agent = False
    try:
        from remedy.runtime.rmb.mode import is_local_agent_mode, should_skip_vision_stack

        local_agent = is_local_agent_mode(cfg if isinstance(cfg, dict) else None)
        skip_vision = should_skip_vision_stack(cfg if isinstance(cfg, dict) else None)
    except Exception:
        skip_vision = bool(state.get("vision_suspended")) or running
    vision_suspended = bool(state.get("vision_suspended")) or running or starting

    model_public = spec.to_public()
    if model_path is not None:
        model_public = {
            **model_public,
            "filename": model_path.name,
            "name": (
                model_path.stem
                if model_path.name != spec.filename
                else model_public.get("name")
            ),
            "path": str(model_path),
        }
    return {
        "ok": True,
        "brand": "RMB",
        "brand_full": "Remedy Muscle Bridge",
        "engine": "llama.cpp",
        "enabled": bool(state.get("enabled")),
        "auto_start": bool(state.get("auto_start", True)),
        "installed": installed,
        "running": running or starting,
        "ready": ready,
        "starting": starting,
        "base_url": state.get("base_url"),
        "host": state.get("host"),
        "port": state.get("port"),
        "model_id": state.get("model_id"),
        "model": model_public,
        "model_path": str(model_path) if model_path else None,
        "model_present": model_path is not None,
        "runtime_binary": str(binary) if binary else None,
        "runtime_present": binary is not None,
        "ctx_size": int(state.get("ctx_size") or 8192),
        "n_gpu_layers": state.get("n_gpu_layers"),
        "profile": state.get("profile") or "agent",
        "nvidia": _nvidia_ok(),
        "catalog": catalog_public(),
        "discovered_ggufs": discovered[:24],
        "user_stopped": _user_stopped,
        "not_ready_hint": (
            None
            if ready
            else (
                "Place any GGUF in ~/.remedy/rmb/models/ and click Start RMB"
                if not model_path
                else (
                    "Install llama-server (Local vision runtime once) then Start RMB"
                    if not binary
                    else ("Starting…" if starting else "Start RMB to load the model")
                )
            )
        ),
        "local_agent_mode": local_agent or running,
        "skips_vision_stack": skip_vision or vision_suspended,
        "vision_suspended": vision_suspended,
        "endless_session": {
            "harness_min_pct": 0.55,
            "harness_max_pct": 0.78,
            "ctx_size": int(state.get("ctx_size") or 8192),
            "silent_context": True,
            "note": (
                "Context is automatic — Session Brief + harness keep long sessions "
                "alive without user-facing compress talk. "
                "SmolVLM is unloaded while RMB is running."
            ),
        },
    }


# Knobs that are baked into the llama-server process argv — changing them on
# disk alone does nothing until the process is restarted with the new flags.
_RMB_PROCESS_KEYS = frozenset(
    {
        "ctx_size",
        "model_path",
        "model_id",
        "runtime_binary",
        "runtime_id",
        "n_gpu_layers",
        "port",
        "host",
        "flash_attn",
        "threads",
        "parallel",
        "profile",
    }
)


def _norm_rmb_val(key: str, val: Any) -> Any:
    """Normalize for process-diff comparison."""
    if key in ("ctx_size", "port", "threads", "parallel", "n_gpu_layers"):
        try:
            return int(val) if val is not None and str(val).strip() != "" else val
        except (TypeError, ValueError):
            return val
    if key == "flash_attn":
        return bool(val)
    if key in ("model_path", "runtime_binary", "host", "model_id", "runtime_id", "profile"):
        return str(val or "").strip()
    return val


def sync_context_window_cache(state: dict[str, Any]) -> int:
    """Push configured ctx_size into token budget cache so next turn uses it."""
    ctx = 0
    try:
        ctx = int(state.get("ctx_size") or 0)
    except (TypeError, ValueError):
        ctx = 0
    if ctx < 2048:
        return 0
    try:
        from remedy.nanoswarm.token_nanobot import (
            cache_context_window,
            clear_context_window_cache,
        )

        # Drop stale 8k guesses so resolve_context_window cannot prefer them
        clear_context_window_cache()
        base = str(state.get("base_url") or f"http://{DEFAULT_HOST}:{DEFAULT_CHAT_PORT}/v1")
        mid = str(state.get("model_id") or "")
        mpath = str(state.get("model_path") or "")
        mname = Path(mpath).name if mpath else ""
        if mname.lower().endswith(".gguf"):
            mname = mname[:-5]
        for model_key in (mid, mname, Path(mpath).stem if mpath else "", None):
            if model_key is not None and not str(model_key).strip():
                continue
            cache_context_window(base, model_key, ctx)
            cache_context_window(None, model_key, ctx)
        cache_context_window(base, None, ctx)
    except Exception:
        logger.debug("RMB: context window cache sync failed", exc_info=True)
    return ctx


def apply_rmb_settings(
    patch: dict[str, Any],
    *,
    home_dir: str | Path | None = None,
    cfg: dict[str, Any] | None = None,
    live: bool = True,
    wait_s: float = 120.0,
) -> dict[str, Any]:
    """Merge Settings patch into rmb.json (+ optional config.rmb).

    When *live* is True (default), process-affecting knobs (ctx_size, model,
    GPU layers, port, …) **restart** the managed llama-server so the next chat
    turn uses the new physical n_ctx — not a stale process still on 8k.
    """
    before = merge_state(load_rmb_json(home_dir))
    state = dict(before)
    for key in (
        "enabled",
        "auto_start",
        "host",
        "port",
        "model_id",
        "model_path",
        "runtime_binary",
        "runtime_id",
        "n_gpu_layers",
        "ctx_size",
        "threads",
        "parallel",
        "flash_attn",
        "profile",
    ):
        if key not in patch:
            continue
        # Allow clearing path fields with ""
        if patch[key] is None and key not in ("model_path", "runtime_binary"):
            continue
        if key == "ctx_size" and patch[key] is not None:
            try:
                state[key] = max(2048, min(131072, int(patch[key])))
            except (TypeError, ValueError):
                continue
        elif key == "port" and patch[key] is not None:
            try:
                state[key] = max(1, min(65535, int(patch[key])))
            except (TypeError, ValueError):
                continue
        elif key == "n_gpu_layers" and patch[key] is not None:
            try:
                state[key] = int(patch[key])
            except (TypeError, ValueError):
                continue
        else:
            state[key] = patch[key] if patch[key] is not None else ""
    if "profile" in patch and patch["profile"] in RMB_PROFILES:
        prof = RMB_PROFILES[str(patch["profile"])]
        if "ctx_size" not in patch:
            state["ctx_size"] = prof.get("ctx_size", state.get("ctx_size"))
        if "n_gpu_layers" not in patch:
            state["n_gpu_layers"] = prof.get("n_gpu_layers", state.get("n_gpu_layers"))
    # Keep base_url in sync with host/port
    host = str(state.get("host") or DEFAULT_HOST)
    try:
        port = int(state.get("port") or DEFAULT_CHAT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_CHAT_PORT
    state["host"] = host
    state["port"] = port
    state["base_url"] = f"http://{host}:{port}/v1"
    state = merge_state(state)
    save_rmb_json(state, home_dir)

    # Which process knobs actually differ after merge (includes profile side-effects)?
    changed_process: list[str] = []
    for key in _RMB_PROCESS_KEYS:
        if _norm_rmb_val(key, before.get(key)) != _norm_rmb_val(key, state.get(key)):
            changed_process.append(key)

    enabled_now = bool(state.get("enabled"))
    enabled_before = bool(before.get("enabled"))
    was_running = False
    with contextlib.suppress(Exception):
        was_running = bool(is_running(home_dir, force=True, require_http=False)) or bool(
            managed_process_alive()
        )

    # Budget cache always tracks configured ctx (even before restart completes)
    live_ctx = sync_context_window_cache(state)

    # Mirror into config.toml
    try:
        from remedy.interfaces.api_support import (
            _find_config_path,
            _write_config,
            invalidate_config_cache,
            load_config,
        )

        invalidate_config_cache()
        if cfg is not None:
            cfg = dict(cfg)
        else:
            disk0 = load_config()
            cfg = dict(disk0) if isinstance(disk0, dict) else {}
        cfg["rmb"] = {
            "enabled": state.get("enabled"),
            "auto_start": state.get("auto_start"),
            "base_url": state.get("base_url"),
            "model_id": state.get("model_id"),
            "profile": state.get("profile"),
            "ctx_size": state.get("ctx_size"),
        }
        if state.get("enabled") and patch.get("use_as_chat_provider"):
            cfg["llm_provider"] = "rmb"
            cfg["llm_base_url"] = state.get("base_url")
            mid = str(state.get("model_id") or DEFAULT_RMB_MODEL_ID)
            cfg["llm_model"] = get_model_spec(mid).filename.replace(".gguf", "")
            cfg["harness_mode"] = cfg.get("harness_mode") or "auto"
            cfg["harness_min_context_pct"] = 0.55
            cfg["harness_max_context_pct"] = 0.78
            # Local agent must write/run tools without click-gating every step
            cfg["approval_mode"] = "auto"
            vision = (
                dict(cfg.get("vision") or {})
                if isinstance(cfg.get("vision"), dict)
                else {}
            )
            vision["force_decode"] = False
            vision["auto_start"] = False
            cfg["vision"] = vision
        path = _find_config_path()
        if path is not None:
            disk = load_config()
            if isinstance(disk, dict):
                disk["rmb"] = cfg["rmb"]
                if state.get("enabled") and patch.get("use_as_chat_provider"):
                    disk["llm_provider"] = cfg["llm_provider"]
                    disk["llm_base_url"] = cfg["llm_base_url"]
                    disk["llm_model"] = cfg["llm_model"]
                    disk["harness_min_context_pct"] = 0.55
                    disk["harness_max_context_pct"] = 0.78
                    disk["approval_mode"] = "auto"
                    v = (
                        dict(disk.get("vision") or {})
                        if isinstance(disk.get("vision"), dict)
                        else {}
                    )
                    v["force_decode"] = False
                    v["auto_start"] = False
                    disk["vision"] = v
                # When chat is already RMB, keep model/base_url + harness aligned
                # with live host so next turn doesn't use stale config.
                elif str(disk.get("llm_provider") or "").lower() == "rmb":
                    disk["llm_base_url"] = state.get("base_url")
                    mid = str(state.get("model_id") or DEFAULT_RMB_MODEL_ID)
                    if state.get("model_path"):
                        stem = Path(str(state["model_path"])).stem
                        if stem:
                            disk["llm_model"] = stem
                    else:
                        disk["llm_model"] = get_model_spec(mid).filename.replace(
                            ".gguf", ""
                        )
                    disk["harness_min_context_pct"] = 0.55
                    disk["harness_max_context_pct"] = 0.78
                    # Full local power: don't leave ask-mode blocking every write
                    if str(disk.get("approval_mode") or "ask").lower() == "ask":
                        disk["approval_mode"] = "auto"
                    cfg["llm_provider"] = "rmb"
                    cfg["llm_base_url"] = disk["llm_base_url"]
                    cfg["llm_model"] = disk["llm_model"]
                _write_config(path, disk)
                invalidate_config_cache()
                # Hot-apply approvals process mode
                with contextlib.suppress(Exception):
                    from remedy.core.approvals import APPROVALS

                    if str(disk.get("approval_mode") or "").lower() == "auto":
                        APPROVALS.set_mode("auto")
    except Exception:
        logger.debug("config mirror failed", exc_info=True)

    live_meta: dict[str, Any] = {
        "live": bool(live),
        "process_keys_changed": changed_process,
        "restarted": False,
        "stopped": False,
        "started": False,
        "was_running": was_running,
        "ctx_size_config": live_ctx or int(state.get("ctx_size") or 0),
        "ctx_size_live": None,
        "live_error": None,
    }

    if live:
        try:
            # Disable → stop immediately
            if enabled_before and not enabled_now:
                stop_rmb_server(home_dir=home_dir, resume_vision=True)
                live_meta["stopped"] = True
            # Process knobs changed while enabled (or still running)
            elif enabled_now and changed_process and was_running:
                logger.info(
                    "RMB live apply: restarting for %s",
                    ",".join(changed_process),
                )
                stop_rmb_server(home_dir=home_dir, resume_vision=False)
                live_meta["stopped"] = True
                start = start_rmb_server(home_dir=home_dir, wait_s=float(wait_s))
                live_meta["started"] = bool(start.get("ok"))
                live_meta["restarted"] = True
                if start.get("ok"):
                    live_meta["ctx_size_live"] = start.get("ctx_size")
                    sync_context_window_cache(
                        {**state, "ctx_size": start.get("ctx_size") or state.get("ctx_size")}
                    )
                else:
                    live_meta["live_error"] = start.get("error") or "restart failed"
            # Enabled but not running: start so settings take effect now
            elif enabled_now and (not was_running) and (
                changed_process
                or patch.get("enabled") is True
                or patch.get("use_as_chat_provider")
            ):
                start = start_rmb_server(home_dir=home_dir, wait_s=float(wait_s))
                live_meta["started"] = bool(start.get("ok") or start.get("starting"))
                if start.get("ok"):
                    live_meta["ctx_size_live"] = start.get("ctx_size")
                    sync_context_window_cache(
                        {**state, "ctx_size": start.get("ctx_size") or state.get("ctx_size")}
                    )
                elif not start.get("starting"):
                    live_meta["live_error"] = start.get("error")
            # Ctx-only cache bump when process already matches (no restart needed)
            elif not changed_process and live_ctx:
                live_meta["ctx_size_live"] = live_ctx
        except Exception as exc:
            logger.exception("RMB live apply failed")
            live_meta["live_error"] = str(exc)

    status = get_rmb_status(cfg)
    status["live_apply"] = live_meta
    # Surface a clear note for the UI when restart happened
    if live_meta.get("restarted"):
        if live_meta.get("started"):
            status["live_note"] = (
                f"RMB restarted live — ctx_size={live_meta.get('ctx_size_live') or live_meta.get('ctx_size_config')} "
                f"(changed: {', '.join(changed_process) or 'settings'})"
            )
        else:
            status["live_note"] = (
                "Settings saved but RMB restart failed: "
                f"{live_meta.get('live_error') or 'unknown'}"
            )
    elif live_meta.get("started"):
        status["live_note"] = "RMB started with current settings"
    elif live_meta.get("stopped") and not enabled_now:
        status["live_note"] = "RMB stopped (disabled in settings)"
    return status
