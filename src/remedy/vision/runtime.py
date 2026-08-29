"""Manage the local llama-server process for the visual decoder."""

from __future__ import annotations

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

from remedy.vision.catalog import DEFAULT_HOST, DEFAULT_PORT
from remedy.vision.config import load_vision_json, save_vision_json
from remedy.vision.install import runtime_binary_path

logger = logging.getLogger(__name__)

_proc: subprocess.Popen[Any] | None = None
_last_used: float = 0.0

# Short-lived probe cache — Settings + status poll this often; never block the
# asyncio event loop for multi-second urlopen timeouts on a dead vision port.
# Asymmetric TTL: stay fresh while running (catch crashes), linger longer when
# the port is closed so StatusBar (20s then 45s) + download-bar (45s) polls do
# not re-burn a Windows SYN-drop (~150ms) on closed :8740. Live 2026-08-27:
# 12s NEG TTL expired before every chrome poll, so GET /api/vision/status
# stayed ~150ms (1022ms on the 5:52pm CT restart stampede).
_running_cache: dict[str, Any] = {"ts": 0.0, "value": False, "key": ""}
_RUNNING_CACHE_TTL_S = 2.5
_RUNNING_CACHE_NEG_TTL_S = 60.0
_NOT_RUNNING_LOG_INTERVAL_S = 30.0
_not_running_log_ts = 0.0
_PORT_OPEN_TIMEOUT_S = 0.05
_HEALTH_TIMEOUT_S = 0.35
# vision.json mtime cache — avoid re-reading JSON on every is_running() call
_vision_json_cache: dict[str, Any] = {"path": "", "mtime": -1.0, "data": {}}


def _proc_ref() -> subprocess.Popen[Any] | None:
    """Snapshot the global Popen handle (stop_server may clear it concurrently)."""
    return _proc


def _port_open(host: str, port: int, timeout: float = _PORT_OPEN_TIMEOUT_S) -> bool:
    """Fail-fast TCP. Windows often drops the SYN to a closed loopback port
    instead of refusing, so the timeout *is* the cost of a miss."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health(base_url: str, timeout: float = _HEALTH_TIMEOUT_S) -> bool:
    """HTTP probe of llama-server. Keep timeouts tiny — callers poll often."""
    from remedy.core.security import is_loopback_service_url, urlopen_no_redirect

    base = (base_url or "").rstrip("/")
    # Fail closed on poisoned vision.json / side config (metadata, LAN).
    if not base or not is_loopback_service_url(base):
        return False
    url = base + "/models"
    try:
        req = Request(url, headers={"User-Agent": "RemedyAI-vision/1.0"})
        with urlopen_no_redirect(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        # Some builds only expose /v1/models
        try:
            alt = (
                base + "/v1/models"
                if not base.endswith("/v1")
                else base + "/models"
            )
            req = Request(alt, headers={"User-Agent": "RemedyAI-vision/1.0"})
            with urlopen_no_redirect(req, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except Exception:
            return False


def _load_vision_json_cached(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Load vision.json using mtime+size — skip disk parse when unchanged.

    Windows often keeps st_mtime at 1s resolution; include size and mtime_ns
    so rapid rewrite (tests / Settings save) still invalidates.
    """
    from remedy.vision.config import vision_json_path

    path = vision_json_path(home_dir)
    path_s = str(path)
    try:
        if path.is_file():
            st = path.stat()
            mtime = float(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
            size = int(st.st_size)
        else:
            mtime = -1.0
            size = -1
    except OSError:
        mtime = -1.0
        size = -1
    if (
        _vision_json_cache.get("path") == path_s
        and float(_vision_json_cache.get("mtime") or -2) == mtime
        and int(_vision_json_cache.get("size") or -2) == size
    ):
        data = _vision_json_cache.get("data")
        return dict(data) if isinstance(data, dict) else {}
    state = load_vision_json(home_dir)
    _vision_json_cache["path"] = path_s
    _vision_json_cache["mtime"] = mtime
    _vision_json_cache["size"] = size
    _vision_json_cache["data"] = state
    # Hand out a copy, exactly as the cache-hit path above does: a caller that
    # mutates its result must not rewrite what every later is_running() reads.
    return dict(state)


def is_running(
    home_dir: str | Path | None = None,
    *,
    force: bool = False,
    require_http: bool = False,
) -> bool:
    """Return whether the vision decoder appears to be accepting connections.

    Fast path: in-process child alive + TCP port open (sub-ms).
    HTTP /models is only used when the port is open and we need a stronger
    signal, or when ``require_http=True`` (e.g. right after start_server).
    Results are cached briefly so Settings/status polls cannot freeze the API.
    """
    global _proc
    state = _load_vision_json_cached(home_dir)
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    cache_key = f"{host}:{port}:{base}"
    now = time.time()
    ttl = (
        _RUNNING_CACHE_TTL_S
        if _running_cache.get("value")
        else _RUNNING_CACHE_NEG_TTL_S
    )
    if (
        not force
        and _running_cache.get("key") == cache_key
        and (now - float(_running_cache.get("ts") or 0)) < ttl
    ):
        return bool(_running_cache.get("value"))

    proc = _proc_ref()
    child_alive = proc is not None and proc.poll() is None
    port_up = _port_open(host, port, timeout=_PORT_OPEN_TIMEOUT_S)

    if child_alive and port_up:
        # Child we own + port open → treat as running without HTTP (avoids
        # stalling the desktop while the model is still loading weights).
        ok = True if not require_http else _health(base)
    elif port_up:
        # External/orphan llama-server — cheap HTTP, only because port is open.
        ok = _health(base) if require_http or not child_alive else True
        if not require_http and not child_alive:
            # Port open is enough for UI "running"; full health on demand.
            ok = True
    elif child_alive:
        # Process exists but not listening yet (still starting).
        ok = False
    else:
        # Nothing listening — do NOT urlopen a dead port (was ~4s of freezes).
        ok = False

    _running_cache["ts"] = now
    _running_cache["value"] = ok
    _running_cache["key"] = cache_key
    if not ok:
        global _not_running_log_ts
        if (now - float(_not_running_log_ts or 0)) >= _NOT_RUNNING_LOG_INTERVAL_S:
            _not_running_log_ts = now
            logger.debug(
                "vision not running host=%s port=%s child=%s port_up=%s",
                host,
                port,
                child_alive,
                port_up,
            )
    return ok


def invalidate_running_cache() -> None:
    """Clear probe cache after start/stop so the next read is fresh."""
    _running_cache["ts"] = 0.0
    _running_cache["value"] = False
    _running_cache["key"] = ""
    _vision_json_cache["mtime"] = -2.0
    _vision_json_cache["data"] = {}


def mark_used() -> None:
    global _last_used
    _last_used = time.time()


def last_used_age_s() -> float | None:
    """Seconds since last vision/nano use of llama-server, or None if never."""
    if _last_used <= 0:
        return None
    return max(0.0, time.time() - _last_used)


def maybe_idle_stop(
    home_dir: str | Path | None = None,
    *,
    idle_stop_s: int | float | None = None,
) -> dict[str, Any]:
    """Stop llama-server after idle_stop_s without use (saves RAM/GPU).

    Next ensure_server / decode / nano local call wakes it again.
    idle_stop_s <= 0 disables. Default from vision.json / config (600).
    """
    if idle_stop_s is None:
        try:
            state = load_vision_json(home_dir)
            idle_stop_s = int(state.get("idle_stop_s") or 600)
        except Exception:
            idle_stop_s = 600
    try:
        idle_stop_s = int(idle_stop_s or 0)
    except (TypeError, ValueError):
        idle_stop_s = 600
    if idle_stop_s <= 0:
        return {"ok": True, "stopped": False, "reason": "disabled"}
    if not is_running(home_dir, force=False):
        return {"ok": True, "stopped": False, "reason": "not_running"}
    age = last_used_age_s()
    # If never marked, use start time approximation: don't stop immediately
    if age is None:
        mark_used()
        return {"ok": True, "stopped": False, "reason": "first_mark"}
    if age < float(idle_stop_s):
        return {
            "ok": True,
            "stopped": False,
            "reason": "active",
            "idle_s": round(age, 1),
            "limit_s": idle_stop_s,
        }
    logger.info(
        "Local model idle for %.0fs (limit %ss) — stopping llama-server",
        age,
        idle_stop_s,
    )
    result = stop_server(home_dir=home_dir)
    result["reason"] = "idle_timeout"
    result["idle_s"] = round(age, 1)
    return result


_idle_thread_started = False
_idle_lock = threading.Lock()


def reset_idle_watcher() -> None:
    """Reset the idle watcher flag so it can be re-created after stop/restart."""
    global _idle_thread_started
    with _idle_lock:
        _idle_thread_started = False


def ensure_idle_watcher(home_dir: str | Path | None = None) -> None:
    """Background poller: stop server after idle_stop_s. Safe to call often."""
    global _idle_thread_started
    with _idle_lock:
        if _idle_thread_started:
            return
        _idle_thread_started = True

    # Also start MDL idle watcher so non-resident tiers auto-stop.
    with contextlib.suppress(Exception):
        from remedy.runtime.mdl_runtime import ensure_idle_watcher as _mdl_watcher

        _mdl_watcher(resident_first=False)

    def _loop() -> None:
        while True:
            try:
                time.sleep(30.0)
                idle_s = 600
                try:
                    from remedy.interfaces.api_support import load_config
                    from remedy.vision.config import vision_section_from_config

                    cfg = load_config()
                    vcfg = vision_section_from_config(cfg if isinstance(cfg, dict) else {})
                    idle_s = int(vcfg.get("idle_stop_s") or 600)
                    hd = None
                    if isinstance(cfg, dict) and cfg.get("home_dir"):
                        hd = cfg.get("home_dir")
                    maybe_idle_stop(hd, idle_stop_s=idle_s)
                except Exception:
                    maybe_idle_stop(home_dir, idle_stop_s=idle_s)
            except Exception:
                logger.debug("idle watcher tick failed", exc_info=True)

    t = threading.Thread(target=_loop, name="remedy-local-idle-stop", daemon=True)
    t.start()


def start_server(
    *,
    home_dir: str | Path | None = None,
    n_gpu_layers: int = -1,
    wait_s: float = 60.0,
    ignore_rmb: bool = False,
) -> dict[str, Any]:
    """Start llama-server if not already healthy.

    Auto-activates the prebundled pinned SmolVLM2 stack when
    ``vision.json`` is missing or points at missing files — no network.

    Hard skip when RMB owns the local host (running or vision_suspended),
    unless *ignore_rmb* (CPU-only observe wake, n_gpu_layers=0).
    """
    global _proc
    # Exclusive with RMB: never load SmolVLM while RMB chat server is up
    try:
        from remedy.runtime.rmb.mode import should_skip_vision_stack

        cfg_hint: dict[str, Any] | None = None
        if home_dir is not None:
            cfg_hint = {"home_dir": str(home_dir)}
        if (not ignore_rmb) and should_skip_vision_stack(cfg_hint):
            logger.info(
                "Skipping SmolVLM start_server — RMB exclusive host "
                "(running or vision_suspended)"
            )
            return {
                "ok": False,
                "skipped": True,
                "reason": "rmb_exclusive_host",
                "error": "SmolVLM disabled while RMB is running",
            }
    except Exception:
        pass
    state = load_vision_json(home_dir)
    # Ignore retired local model pins left in vision.json (e.g. qwen2.5-vl-3b).
    # Prefer soft-migrate to the product default rather than KeyError spam in logs.
    mid = str((state or {}).get("model_id") or "")
    if mid and mid not in ("", "smolvlm2-2.2b"):
        from remedy.vision.catalog import DEFAULT_MODEL_ID, get_model_spec

        try:
            get_model_spec(mid)
        except Exception:
            # Retired / unknown pin: clear paths so bundle activate can rebind,
            # and rewrite model_id to the product default (no KeyError spam).
            default_mid = DEFAULT_MODEL_ID or "smolvlm2-2.2b"
            if isinstance(state, dict) and state:
                state = dict(state)
                state["model_id"] = default_mid
                state.pop("model_path", None)
                state.pop("mmproj_path", None)
                state.pop("pid", None)
                with contextlib.suppress(Exception):
                    save_vision_json(state, home_dir)
                logger.info(
                    "Migrated retired vision model_id %r → %r",
                    mid,
                    default_mid,
                )
            else:
                state = {}
    if not state or not Path(str(state.get("model_path") or "")).is_file():
        try:
            from remedy.runtime.bundle import activate_local_bundle

            act = activate_local_bundle(home_dir, enabled=True)
            if act.get("ok"):
                state = load_vision_json(home_dir)
            elif not state:
                return {
                    "ok": False,
                    "error": act.get("error")
                    or "Local model not activated (no vision.json / bundle)",
                }
        except Exception as e:
            if not state:
                return {"ok": False, "error": f"Bundle activate failed: {e}"}

    if not state:
        return {"ok": False, "error": "Local model not ready (no vision.json)"}

    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    model_path = state.get("model_path")
    mmproj_path = state.get("mmproj_path")
    if not model_path or not Path(str(model_path)).is_file():
        return {"ok": False, "error": f"Model file missing: {model_path}"}
    if not mmproj_path or not Path(str(mmproj_path)).is_file():
        return {"ok": False, "error": f"mmproj file missing: {mmproj_path}"}

    if is_running(home_dir, force=True, require_http=True):
        mark_used()
        ensure_idle_watcher(home_dir)
        return {"ok": True, "already_running": True, "base_url": base, "pid": _pid()}

    binary = None
    # Prefer explicit path written by activate_local_bundle
    rb = state.get("runtime_binary")
    if rb and Path(str(rb)).is_file():
        binary = Path(str(rb))
    if binary is None:
        binary = runtime_binary_path(home_dir)
    if binary is None:
        try:
            from remedy.runtime.bundle import runtime_binary_from_bundle

            binary = runtime_binary_from_bundle(str(state.get("runtime_id") or ""))
        except Exception:
            binary = None
    if binary is None:
        return {"ok": False, "error": "llama-server binary not found"}

    # Find free port if default busy
    if _port_open(host, port) and not _health(base):
        for p in range(port + 1, port + 20):
            if not _port_open(host, p):
                port = p
                base = f"http://{host}:{port}/v1"
                state["port"] = port
                state["base_url"] = base
                save_vision_json(state, home_dir)
                break

    cmd = [
        str(binary),
        "-m",
        str(model_path),
        "--mmproj",
        str(mmproj_path),
        "--host",
        host,
        "--port",
        str(port),
        "--ctx-size",
        "4096",
    ]
    if n_gpu_layers is not None:
        cmd.extend(["-ngl", str(int(n_gpu_layers))])

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # Re-check exclusive host immediately before spawn (RMB may have started mid-path)
    try:
        from remedy.runtime.rmb.mode import should_skip_vision_stack

        cfg_hint2: dict[str, Any] | None = None
        if home_dir is not None:
            cfg_hint2 = {"home_dir": str(home_dir)}
        if (not ignore_rmb) and should_skip_vision_stack(cfg_hint2):
            logger.info("Aborting SmolVLM spawn — RMB exclusive host re-check")
            return {
                "ok": False,
                "skipped": True,
                "reason": "rmb_exclusive_host",
                "error": "SmolVLM disabled while RMB is running",
            }
    except Exception:
        logger.warning("RMB exclusive-host re-check failed", exc_info=True)
        if not ignore_rmb:
            return {
                "ok": False,
                "skipped": True,
                "reason": "rmb_check_failed",
                "error": "Could not confirm RMB is off; not starting SmolVLM",
            }

    logger.info("Starting vision llama-server: %s", " ".join(cmd))
    try:
        _proc = subprocess.Popen(
            cmd,
            cwd=str(binary.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as e:
        return {"ok": False, "error": f"Failed to start llama-server: {e}"}

    invalidate_running_cache()
    deadline = time.time() + wait_s
    while time.time() < deadline:
        proc = _proc_ref()
        if proc is None:
            invalidate_running_cache()
            return {
                "ok": False,
                "error": "llama-server handle cleared during start (stopped concurrently)",
            }
        if proc.poll() is not None:
            invalidate_running_cache()
            return {
                "ok": False,
                "error": f"llama-server exited early (code {proc.returncode})",
            }
        # Port first (cheap); then a short HTTP probe once listening.
        if _port_open(host, port) and (
            _health(base, timeout=0.6) or time.time() > deadline - 2
        ):
            mark_used()
            ensure_idle_watcher(home_dir)
            state["pid"] = proc.pid
            save_vision_json(state, home_dir)
            invalidate_running_cache()
            # Seed cache as running so status polls stay cheap after start.
            is_running(home_dir, force=True)
            return {
                "ok": True,
                "already_running": False,
                "base_url": base,
                "pid": proc.pid,
            }
        time.sleep(0.4)

    invalidate_running_cache()
    return {"ok": False, "error": f"llama-server did not become healthy within {wait_s}s"}


def _kill_pid_tree(pid: int, *, force: bool = True) -> bool:
    """Terminate a process (and its children on Windows). Returns True if we tried."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # /T = process tree (llama-server children / helpers)
            args = ["taskkill", "/PID", str(pid), "/T"]
            if force:
                args.insert(1, "/F")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                creationflags=creationflags,
            )
            return True
        # POSIX: terminate process group when possible
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, 15)  # SIGTERM
            time.sleep(0.3)
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 0)  # still alive?
                if force:
                    os.kill(pid, 9)  # SIGKILL
            return True
    except (OSError, subprocess.TimeoutExpired, ValueError):
        logger.debug("kill_pid_tree failed for %s", pid, exc_info=True)
    return False


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # tasklist is slow; use OpenProcess via ctypes is heavy — poll with kill 0 pattern
        try:
            # Windows: os.kill exists in 3.x and raises OSError if gone
            os.kill(pid, 0)
            return True
        except (OSError, SystemError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _windows_process_name(pid: int) -> str:
    """Lowercased ProcessName, or empty if gone / denied / not Windows."""
    if os.name != "nt" or pid <= 0:
        return ""
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
        timeout=5,
        creationflags=creationflags,
        check=False,
    )
    return (out.stdout or "").strip().lower()


def _looks_like_llama_server(pid: int) -> bool:
    """Best-effort check that *pid* is our vision binary (avoid killing strangers)."""
    if pid <= 0:
        return False
    if os.name != "nt":
        # /proc available on Linux
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            text = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
            return "llama-server" in text or "llama_server" in text
        except OSError:
            return True  # still allow terminate if we recorded the pid ourselves
    try:
        name = _windows_process_name(pid)
        if not name:
            return False
        return "llama" in name
    except (OSError, subprocess.TimeoutExpired):
        return True


def stop_server(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Stop the vision llama-server managed by this process and any recorded PID.

    On desktop quit / API shutdown this is the deliberate cleanup path so
    llama-server does not keep using RAM/GPU after Remedy exits.
    """
    global _proc
    killed = False
    pids: list[int] = []
    invalidate_running_cache()

    proc = _proc_ref()
    if proc is not None:
        with contextlib.suppress(Exception):
            if proc.poll() is None and proc.pid:
                pids.append(int(proc.pid))
        # Prefer graceful terminate via Popen handle first
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    killed = True
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(Exception):
                        proc.kill()
                        proc.wait(timeout=3)
                    killed = True
        _proc = None

    state = load_vision_json(home_dir)
    recorded = state.get("pid")
    if recorded is not None:
        with contextlib.suppress(TypeError, ValueError):
            pids.append(int(recorded))

    # Unique PIDs; kill trees for anything still alive
    seen: set[int] = set()
    for pid in pids:
        if pid in seen:
            continue
        seen.add(pid)
        if not _pid_is_alive(pid):
            continue
        # Only force-kill if it looks like llama-server (or we own the Popen handle pid).
        # Empty Windows ProcessName (access denied / gone) is not a stranger —
        # skipping that leaked llama-server after idle-stop. Linux already
        # identified non-llama via /proc, so a False match stays a skip.
        if pid == recorded and not _looks_like_llama_server(pid):
            skip = True
            if os.name == "nt":
                name = ""
                with contextlib.suppress(Exception):
                    name = _windows_process_name(pid)
                skip = bool(name)
            if skip:
                logger.warning(
                    "Vision pid %s in vision.json does not look like llama-server; skipping kill",
                    pid,
                )
                continue
        if _kill_pid_tree(pid, force=True):
            killed = True
            logger.info("Stopped vision decoder process pid=%s", pid)

    # Clear pid from side state
    if state.get("pid") is not None:
        state.pop("pid", None)
        with contextlib.suppress(Exception):
            save_vision_json(state, home_dir)

    return {"ok": True, "stopped": killed, "pids": list(seen)}


def shutdown_vision_for_exit(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Best-effort stop for process exit (API lifespan, atexit, desktop tree-kill)."""
    try:
        result = stop_server(home_dir=home_dir)
        logger.info("Vision decoder shutdown for exit: %s", result)
        return result
    except Exception as e:
        logger.warning("Vision decoder shutdown for exit failed: %s", e)
        return {"ok": False, "error": str(e), "stopped": False}


def _pid() -> int | None:
    proc = _proc_ref()
    if proc is not None and proc.poll() is None:
        return proc.pid
    return None
