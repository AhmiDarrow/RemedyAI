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
from urllib.request import Request, urlopen

from remedy.vision.catalog import DEFAULT_HOST, DEFAULT_PORT
from remedy.vision.config import load_vision_json, save_vision_json
from remedy.vision.install import runtime_binary_path

logger = logging.getLogger(__name__)

_proc: subprocess.Popen[Any] | None = None
_last_used: float = 0.0

# Short-lived probe cache — Settings + status poll this often; never block the
# asyncio event loop for multi-second urlopen timeouts on a dead vision port.
_running_cache: dict[str, Any] = {"ts": 0.0, "value": False, "key": ""}
_RUNNING_CACHE_TTL_S = 2.5
_HEALTH_TIMEOUT_S = 0.35


def _port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health(base_url: str, timeout: float = _HEALTH_TIMEOUT_S) -> bool:
    """HTTP probe of llama-server. Keep timeouts tiny — callers poll often."""
    url = base_url.rstrip("/") + "/models"
    try:
        req = Request(url, headers={"User-Agent": "RemedyAI-vision/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        # Some builds only expose /v1/models
        try:
            alt = (
                base_url.rstrip("/") + "/v1/models"
                if not base_url.rstrip("/").endswith("/v1")
                else base_url.rstrip("/") + "/models"
            )
            req = Request(alt, headers={"User-Agent": "RemedyAI-vision/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except Exception:
            return False


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
    state = load_vision_json(home_dir)
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    cache_key = f"{host}:{port}:{base}"
    now = time.time()
    if (
        not force
        and _running_cache.get("key") == cache_key
        and (now - float(_running_cache.get("ts") or 0)) < _RUNNING_CACHE_TTL_S
    ):
        return bool(_running_cache.get("value"))

    child_alive = _proc is not None and _proc.poll() is None
    port_up = _port_open(host, port)

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
        logger.debug("vision not running host=%s port=%s child=%s port_up=%s", host, port, child_alive, port_up)
    return ok


def invalidate_running_cache() -> None:
    """Clear probe cache after start/stop so the next read is fresh."""
    _running_cache["ts"] = 0.0
    _running_cache["value"] = False
    _running_cache["key"] = ""


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


def ensure_idle_watcher(home_dir: str | Path | None = None) -> None:
    """Background poller: stop server after idle_stop_s. Safe to call often."""
    global _idle_thread_started
    with _idle_lock:
        if _idle_thread_started:
            return
        _idle_thread_started = True

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
) -> dict[str, Any]:
    """Start llama-server if not already healthy.

    Auto-activates the prebundled (or legacy) pinned Qwen stack when
    ``vision.json`` is missing or points at missing files — no network.
    """
    global _proc
    state = load_vision_json(home_dir)
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
        if _proc.poll() is not None:
            invalidate_running_cache()
            return {
                "ok": False,
                "error": f"llama-server exited early (code {_proc.returncode})",
            }
        # Port first (cheap); then a short HTTP probe once listening.
        if _port_open(host, port) and (
            _health(base, timeout=0.6) or time.time() > deadline - 2
        ):
            mark_used()
            ensure_idle_watcher(home_dir)
            state["pid"] = _proc.pid
            save_vision_json(state, home_dir)
            invalidate_running_cache()
            # Seed cache as running so status polls stay cheap after start.
            is_running(home_dir, force=True)
            return {
                "ok": True,
                "already_running": False,
                "base_url": base,
                "pid": _proc.pid,
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
        name = (out.stdout or "").strip().lower()
        return "llama-server" in name or name in ("llama-server", "llama_server")
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

    if _proc is not None:
        with contextlib.suppress(Exception):
            if _proc.poll() is None and _proc.pid:
                pids.append(int(_proc.pid))
        # Prefer graceful terminate via Popen handle first
        if _proc.poll() is None:
            with contextlib.suppress(Exception):
                _proc.terminate()
                try:
                    _proc.wait(timeout=5)
                    killed = True
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(Exception):
                        _proc.kill()
                        _proc.wait(timeout=3)
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
        # Only force-kill if it looks like llama-server (or we own the Popen handle pid)
        if pid == recorded and not _looks_like_llama_server(pid):
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
    if _proc is not None and _proc.poll() is None:
        return _proc.pid
    return None
