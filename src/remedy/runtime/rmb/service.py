"""Manage the RMB local chat llama-server (coding + tools).

Separate from vision (port 8740 + mmproj). Brand: RMB — engine: llama-server.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request

from remedy.home import default_home
from remedy.runtime.rmb.autofit import (
    apply_plan_to_state,
    classify_start_failure,
    downgrade_plan,
    last_good_payload,
    plan_autofit,
    plan_from_state,
    probe_hardware,
    probe_live_n_ctx,
    should_autofit,
)
from remedy.runtime.rmb.catalog import (
    DEFAULT_RMB_MODEL_ID,
    RMB_MODELS,
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
from remedy.runtime.rmb.host_profile import (
    apply_host_profile_to_state,
    detect_gguf_host_profile,
    model_switch_should_refit,
)

logger = logging.getLogger(__name__)

_proc: subprocess.Popen[Any] | None = None
_last_used: float = 0.0
_lock = threading.Lock()
_atexit_registered = False
_starting_until: float = 0.0
_user_stopped: bool = False
_discover_ggufs_cache: dict[str, Any] = {"ts": 0.0, "key": "", "value": []}
_DISCOVER_GGUFS_TTL_S = 5.0


def _refresh_user_stopped(home_dir: str | Path | None = None) -> bool:
    """Honor persisted rmb.json user_stopped after API recycle."""
    global _user_stopped
    if _user_stopped:
        return True
    try:
        if bool(load_rmb_json(home_dir).get("user_stopped")):
            _user_stopped = True
            return True
    except Exception:
        pass
    return False


def _persist_user_stopped(
    home_dir: str | Path | None, stopped: bool
) -> None:
    global _user_stopped
    _user_stopped = bool(stopped)
    try:
        st = merge_state(load_rmb_json(home_dir))
        st["user_stopped"] = bool(stopped)
        save_rmb_json(st, home_dir)
    except Exception:
        logger.debug("persist user_stopped failed", exc_info=True)

_running_cache: dict[str, Any] = {"ts": 0.0, "value": False, "key": ""}
_RUNNING_CACHE_TTL_S = 1.5
_HEALTH_TIMEOUT_S = 0.4
# Rock-solid host: background watchdog restarts RMB if it dies mid-session.
_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()
_watchdog_home: str | Path | None = None
_watchdog_fail_streak = 0
_WATCHDOG_INTERVAL_S = 8.0
_WATCHDOG_FAIL_THRESHOLD = 2  # consecutive unhealthy polls before restart
# Loading stall: port open + not healthy longer than this → force restart
_LOADING_STALL_S = 180.0
_loading_since: float = 0.0
# Crash-loop protection: max restarts in a rolling window
_WATCHDOG_RESTART_WINDOW_S = 300.0
_WATCHDOG_MAX_RESTARTS = 4
_watchdog_restart_times: list[float] = []
# Single-flight start: concurrent waiters join one spawn instead of racing
_start_flight_lock = threading.Lock()
_start_flight_active = False
_start_flight_result: dict[str, Any] | None = None
_start_flight_event = threading.Event()
_last_start_error: str | None = None
_last_health_detail: str = ""


def _port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health(base_url: str, timeout: float = _HEALTH_TIMEOUT_S) -> bool:
    """True when llama-server is accepting model queries (not mid-load).

    Probes ``/health`` first. ``/v1/models`` only if ``/health`` is 404.
    HTTP 503 Loading model → not ready.
    """
    global _last_health_detail
    from urllib.error import HTTPError, URLError

    from remedy.core.security import is_loopback_service_url, urlopen_no_redirect

    base = (base_url or "").rstrip("/")
    if not base or not is_loopback_service_url(base):
        _last_health_detail = "bad_url"
        return False
    # Strip trailing /v1 for /health which is usually on the root
    root = base
    if root.endswith("/v1"):
        root = root[:-3]
    # One URL on the happy path. /v1/models only if /health is missing (404).
    health_url = root + "/health"
    models_url = base + "/models" if base.endswith("/v1") else base + "/v1/models"
    last_err = ""
    for url in (health_url, models_url):
        try:
            req = Request(url, headers={"User-Agent": "RemedyAI-RMB/1.0"})
            with urlopen_no_redirect(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if status == 503:
                    _last_health_detail = "loading"
                    return False
                if 200 <= status < 300:
                    try:
                        body = resp.read(256).decode("utf-8", errors="ignore").lower()
                    except Exception:
                        body = ""
                    if body and (
                        '"status":"error"' in body.replace(" ", "")
                        or '"error"' in body[:40]
                    ):
                        last_err = "health_error_body"
                        if url == health_url:
                            continue
                        break
                    if "loading" in body and "ok" not in body:
                        _last_health_detail = "loading"
                        return False
                    _last_health_detail = "ok"
                    return True
        except HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            if code == 503:
                _last_health_detail = "loading"
                return False
            last_err = f"http_{code}"
            if url == health_url and code == 404:
                continue
            break
        except URLError as exc:
            last_err = f"urlerr:{exc.reason!s}"[:80]
            break
        except Exception as exc:
            err = str(exc).lower()
            if "503" in err or "loading" in err:
                _last_health_detail = "loading"
                return False
            last_err = type(exc).__name__
            break
    _last_health_detail = last_err or "unreachable"
    return False


def _note_loading_state(loading: bool) -> None:
    """Track how long the host has been in loading/wedged state."""
    global _loading_since
    now = time.time()
    if loading:
        if _loading_since <= 0:
            _loading_since = now
    else:
        _loading_since = 0.0


def loading_for_s(home_dir: str | Path | None = None) -> float:
    """Seconds the host has been port-up but not healthy (0 if ready/down)."""
    if not is_loading(home_dir):
        return 0.0
    if _loading_since <= 0:
        return 0.0
    return max(0.0, time.time() - _loading_since)


def loading_stalled(
    home_dir: str | Path | None = None,
    *,
    max_s: float = _LOADING_STALL_S,
) -> bool:
    """True when weights never became healthy within *max_s* (wedged load)."""
    return loading_for_s(home_dir) >= max(30.0, float(max_s))


def is_loading(home_dir: str | Path | None = None) -> bool:
    """Port open but health not ready (weights still loading or wedged)."""
    state = merge_state(load_rmb_json(home_dir))
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_CHAT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    if not _port_open(host, port):
        _note_loading_state(False)
        return False
    if _health(base, timeout=0.9):
        _note_loading_state(False)
        return False
    # Port open + not healthy ≈ loading or wedged
    _note_loading_state(True)
    return True


def mark_used() -> None:
    global _last_used
    _last_used = time.time()


def managed_process_alive() -> bool:
    """True when this process holds a live child llama-server."""
    proc = _proc
    return proc is not None and proc.poll() is None


def is_starting() -> bool:
    """True during spawn → healthy window (blocks vision heal/race)."""
    if time.time() < float(_starting_until or 0):
        return True
    return bool(
        managed_process_alive() and not is_running(force=False, require_http=True)
    )


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

    # Dead managed child → drop cache immediately (avoid 2s "still up" lie)
    proc = _proc
    child = proc is not None and proc.poll() is None
    if proc is not None and not child:
        invalidate_cache()
        force = True

    if (
        not force
        and _running_cache.get("key") == key
        and (now - float(_running_cache.get("ts") or 0)) < _RUNNING_CACHE_TTL_S
    ):
        return bool(_running_cache.get("value"))

    port_up = _port_open(host, port)
    if child and port_up:
        ok = True if not require_http else _health(base)
    elif port_up:
        # Prefer HTTP health; bare open port is not enough (exclusive-host safety)
        healthy = _health(base)
        ok = healthy if require_http else (healthy or child)
    else:
        ok = False
    if ok:
        _note_loading_state(False)
    _running_cache["ts"] = now
    _running_cache["value"] = ok
    _running_cache["key"] = key
    return ok


def invalidate_cache() -> None:
    _running_cache["ts"] = 0.0
    _running_cache["value"] = False


def _looks_like_llama_server(pid: int) -> bool:
    """Avoid killing unrelated processes when rmb.json pid is stale.

    Prefer killing on doubt when the process name contains llama / is our
    configured runtime binary — a sticky host is worse than a false positive
    on a developer-named binary.
    """
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            text = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
            return "llama-server" in text or "llama_server" in text or "llama.cpp" in text
        except OSError:
            return True
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # ProcessName + Path (CUDA builds report ProcessName=llama-server)
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
                    "if(-not $p){''}else{"
                    "($p.ProcessName+' '+($p.Path|Out-String)).Trim()}"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=creationflags,
        )
        name = (out.stdout or "").strip().lower()
        if not name:
            return False
        return (
            "llama" in name
            or "llama-server" in name
            or "llama_server" in name
            or name.endswith("server.exe")
            and "llama" in name
        )
    except Exception:
        # If we can't inspect, allow kill when pid matches rmb.json (caller decides)
        return True


def _kill_pid(pid: int) -> bool:
    """Force-kill *pid* (and children on Windows). Returns True if a kill was attempted."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                creationflags=flags,
                timeout=8,
            )
            if r.returncode == 0:
                return True
            # Fallback: PowerShell Stop-Process (taskkill sometimes fails on protected trees)
            r2 = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue",
                ],
                capture_output=True,
                creationflags=flags,
                timeout=8,
            )
            return r2.returncode == 0
        os.kill(pid, 15)
        time.sleep(0.2)
        with contextlib.suppress(Exception):
            os.kill(pid, 9)
        return True
    except Exception:
        return False


def _find_pid_on_port(port: int) -> int | None:
    """Return PID listening on *port*, or None."""
    if port <= 0:
        return None
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            needle = f":{int(port)}"
            for line in (out.stdout or "").splitlines():
                up = line.upper()
                if "LISTENING" not in up and "LISTEN" not in up:
                    continue
                if needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                with contextlib.suppress(ValueError, IndexError):
                    pid = int(parts[-1])
                    if pid > 0:
                        return pid
        else:
            out = subprocess.run(
                ["lsof", "-ti", f":{int(port)}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in (out.stdout or "").splitlines():
                with contextlib.suppress(ValueError):
                    pid = int(line.strip())
                    if pid > 0:
                        return pid
    except Exception:
        logger.debug("RMB: find pid on port failed", exc_info=True)
    return None


def adopt_existing_host(home_dir: str | Path | None = None) -> dict[str, Any]:
    """If a healthy llama-server is already on the RMB port, adopt its PID.

    After API recycle the child Popen handle is lost; adopting prevents a
    second spawn and keeps watchdog/stop working.
    """
    state = merge_state(load_rmb_json(home_dir))
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_CHAT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    if not _health(base, timeout=1.0):
        return {"ok": False, "adopted": False, "reason": "not_healthy"}
    pid = _find_pid_on_port(port)
    if not pid:
        return {"ok": True, "adopted": False, "reason": "healthy_no_pid", "base_url": base}
    state["pid"] = pid
    state["enabled"] = True
    state["vision_suspended"] = True
    save_rmb_json(state, home_dir)
    mark_used()
    invalidate_cache()
    logger.info("RMB: adopted existing host pid=%s on port %s", pid, port)
    return {"ok": True, "adopted": True, "pid": pid, "base_url": base}


def _watchdog_can_restart() -> bool:
    """Crash-loop guard: allow restart only if under max in rolling window."""
    global _watchdog_restart_times
    now = time.time()
    _watchdog_restart_times = [
        t for t in _watchdog_restart_times if (now - t) < _WATCHDOG_RESTART_WINDOW_S
    ]
    return len(_watchdog_restart_times) < _WATCHDOG_MAX_RESTARTS


def _watchdog_record_restart() -> None:
    global _watchdog_restart_times
    _watchdog_restart_times.append(time.time())


def ensure_rmb_watchdog(home_dir: str | Path | None = None) -> None:
    """Start a daemon watchdog that restarts RMB if it dies (auto_start mode)."""
    global _watchdog_thread, _watchdog_home
    _watchdog_home = home_dir
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()

    def _loop() -> None:
        global _watchdog_fail_streak, _last_start_error
        while not _watchdog_stop.wait(_WATCHDOG_INTERVAL_S):
            try:
                if _refresh_user_stopped(_watchdog_home):
                    _watchdog_fail_streak = 0
                    continue
                st = merge_state(load_rmb_json(_watchdog_home))
                if not st.get("enabled", True) or not st.get("auto_start", False):
                    _watchdog_fail_streak = 0
                    continue
                if is_running(_watchdog_home, force=True, require_http=True):
                    _watchdog_fail_streak = 0
                    _note_loading_state(False)
                    # Keep pid in sync if we lost the child handle
                    if not managed_process_alive():
                        with contextlib.suppress(Exception):
                            adopt_existing_host(_watchdog_home)
                    continue
                # Loading is fine unless it stalls past deadline (wedged GPU load)
                if is_loading(_watchdog_home) or is_starting():
                    if not loading_stalled(_watchdog_home):
                        continue
                    logger.warning(
                        "RMB watchdog: loading stalled for %.0fs — force restart",
                        loading_for_s(_watchdog_home),
                    )
                    if not _watchdog_can_restart():
                        logger.error(
                            "RMB watchdog: crash-loop limit (%s/%ss) — not restarting stalled host",
                            _WATCHDOG_MAX_RESTARTS,
                            int(_WATCHDOG_RESTART_WINDOW_S),
                        )
                        continue
                    with contextlib.suppress(Exception):
                        stop_rmb_server(
                            home_dir=_watchdog_home,
                            resume_vision=False,
                            user_intent=False,
                        )
                    _watchdog_fail_streak = 0
                    _watchdog_record_restart()
                    with contextlib.suppress(Exception):
                        r = start_rmb_server(home_dir=_watchdog_home, wait_s=120.0)
                        if not r.get("ok"):
                            _last_start_error = str(r.get("error") or "stall restart failed")
                    continue
                _watchdog_fail_streak += 1
                if _watchdog_fail_streak < _WATCHDOG_FAIL_THRESHOLD:
                    logger.info(
                        "RMB watchdog: unhealthy (%s/%s) detail=%s",
                        _watchdog_fail_streak,
                        _WATCHDOG_FAIL_THRESHOLD,
                        _last_health_detail,
                    )
                    continue
                if not _watchdog_can_restart():
                    logger.error(
                        "RMB watchdog: crash-loop limit hit — backing off "
                        "(%s restarts in %ss). Last error: %s",
                        _WATCHDOG_MAX_RESTARTS,
                        int(_WATCHDOG_RESTART_WINDOW_S),
                        _last_start_error or _last_health_detail,
                    )
                    # Stretch fail streak so we don't log-spam every tick
                    _watchdog_fail_streak = 0
                    continue
                logger.warning(
                    "RMB watchdog: host down — auto-restarting (fail_streak=%s)",
                    _watchdog_fail_streak,
                )
                _watchdog_fail_streak = 0
                # Do not clear user_stopped — only heal if not user-stopped
                if not _user_stopped:
                    _watchdog_record_restart()
                    with contextlib.suppress(Exception):
                        r = start_rmb_server(home_dir=_watchdog_home, wait_s=90.0)
                        if not r.get("ok"):
                            _last_start_error = str(
                                r.get("error") or "watchdog restart failed"
                            )
                            logger.warning(
                                "RMB watchdog: restart failed: %s", _last_start_error
                            )
            except Exception:
                logger.exception("RMB watchdog tick failed")

    _watchdog_thread = threading.Thread(
        target=_loop, name="remedy-rmb-watchdog", daemon=True
    )
    _watchdog_thread.start()
    logger.info("RMB watchdog started (interval=%ss)", _WATCHDOG_INTERVAL_S)


def _kill_listeners_on_port(port: int) -> int:
    """Kill any process listening on *port* (Windows netstat / lsof). Returns kill attempts."""
    if port <= 0:
        return 0
    killed = 0
    try:
        pids: set[int] = set()
        if os.name == "nt":
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            needle = f":{int(port)}"
            for line in (out.stdout or "").splitlines():
                if "LISTENING" not in line.upper() and "LISTEN" not in line.upper():
                    continue
                if needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                with contextlib.suppress(ValueError, IndexError):
                    pids.add(int(parts[-1]))
        else:
            pid = _find_pid_on_port(port)
            if pid:
                pids.add(pid)
        for pid in pids:
            if pid <= 0 or pid == os.getpid():
                continue
            if _kill_pid(pid):
                killed += 1
                logger.info("RMB: killed listener pid=%s on port %s", pid, port)
    except Exception:
        logger.debug("RMB: port listener kill failed", exc_info=True)
    return killed


def _tail_log(path: str | Path | None, *, max_bytes: int = 2500) -> str:
    """Last chunk of llama-server.log for error payloads."""
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.is_file():
            return ""
        data = p.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        text = data.decode("utf-8", errors="replace").strip()
        return text[-max_bytes:]
    except Exception:
        return ""


def _wait_for_port_healthy(
    host: str,
    port: int,
    base: str,
    *,
    timeout_s: float = 90.0,
    poll_s: float = 0.5,
) -> bool:
    """Wait until *base* is healthy or timeout. Used when port is mid-load."""
    deadline = time.time() + max(1.0, float(timeout_s))
    while time.time() < deadline:
        if _user_stopped:
            return False
        if _health(base, timeout=1.0):
            _note_loading_state(False)
            return True
        if not _port_open(host, port):
            return False
        time.sleep(max(0.2, float(poll_s)))
    return _health(base, timeout=1.0)


def _resolve_occupied_port(
    host: str,
    port: int,
    base: str,
    *,
    wait_s: float = 90.0,
) -> dict[str, Any]:
    """Handle port-open-but-not-healthy before spawn.

    Rock-solid partner path:
    - llama mid-load → wait for healthy (do not refuse start)
    - llama wedged / stall → kill and free the port
    - non-llama occupant → kill if it looks safe, else error
    """
    if not _port_open(host, port):
        return {"ok": True, "free": True}
    if _health(base, timeout=1.0):
        return {"ok": True, "already_healthy": True}

    pid = _find_pid_on_port(port)
    looks_llama = bool(pid and _looks_like_llama_server(pid))
    # Always give a loading llama-server time to finish weights
    if looks_llama or pid is None:
        logger.info(
            "RMB: port %s open but not healthy (pid=%s llama=%s) — waiting up to %.0fs",
            port,
            pid,
            looks_llama,
            wait_s,
        )
        _note_loading_state(True)
        if _wait_for_port_healthy(host, port, base, timeout_s=min(wait_s, 120.0)):
            return {"ok": True, "became_healthy": True, "pid": pid}
        logger.warning(
            "RMB: port %s still not healthy after wait — clearing listeners",
            port,
        )

    # Wedged or foreign process — free the port
    if pid and _kill_pid(pid):
        logger.info("RMB: killed occupant pid=%s on port %s", pid, port)
    _kill_listeners_on_port(port)
    # Brief settle so bind succeeds
    for _ in range(20):
        if not _port_open(host, port):
            return {"ok": True, "cleared": True, "pid": pid}
        time.sleep(0.15)
        _kill_listeners_on_port(port)
    if _port_open(host, port) and not _health(base):
        return {
            "ok": False,
            "error": (
                f"Port {port} is in use but not a healthy llama-server and could not "
                "be freed. Stop the other process or change RMB port in Settings."
            ),
            "port": port,
            "pid": pid,
        }
    return {"ok": True, "cleared": True, "pid": pid}


def _model_search_roots(home_dir: str | Path | None) -> list[Path]:
    roots = [
        models_dir(home_dir),
        default_home() / "models",
        default_home() / "rmb" / "models",
        Path.home() / "Downloads",
        # Last: leftover sibling-product folder — never preferred over ~/.remedy/rmb
        Path.home() / "Remedy Muscle Bridge" / "models",
    ]
    # Optional extra dirs via env (semicolon-separated on Windows)
    extra = (os.environ.get("REMEDY_RMB_MODEL_DIRS") or "").strip()
    if extra:
        for part in extra.replace(";", os.pathsep).split(os.pathsep):
            p = Path(part.strip())
            if p.is_dir():
                roots.append(p)
    # De-dupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        try:
            key = str(r.resolve()).lower() if r.exists() else str(r).lower()
        except OSError:
            key = str(r).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


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
        from remedy.runtime.catalog import default_runtime_id, host_runtime_ids

        for rid in (
            default_runtime_id(prefer_gpu=True),
            default_runtime_id(prefer_gpu=False),
            *host_runtime_ids(),
        ):
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
    cache_key = str(home_dir or "")
    now = time.time()
    if (
        _discover_ggufs_cache.get("key") == cache_key
        and (now - float(_discover_ggufs_cache.get("ts") or 0)) < _DISCOVER_GGUFS_TTL_S
    ):
        cached = _discover_ggufs_cache.get("value")
        if isinstance(cached, list):
            return list(cached)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(p: Path) -> None:
        if not p.is_file() or p.suffix.lower() != ".gguf":
            return
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            return
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
                "dir": str(p.parent),
                # Stem id for status-bar / session LLM switch (stable, no path sep)
                "id": p.stem,
            }
        )

    # Always include sticky configured path (even outside search roots)
    try:
        st = merge_state(load_rmb_json(home_dir))
        sticky = str(st.get("model_path") or "").strip()
        if sticky:
            _add(Path(sticky))
    except Exception:
        pass

    for root in _model_search_roots(home_dir):
        if not root.is_dir():
            continue
        try:
            for p in sorted(root.glob("*.gguf")):
                _add(p)
        except Exception:
            continue
    # Prefer larger / more recently used first for UI (size desc, then name)
    out.sort(key=lambda g: (-float(g.get("size_gb") or 0), str(g.get("name") or "").lower()))
    _discover_ggufs_cache["ts"] = now
    _discover_ggufs_cache["key"] = cache_key
    _discover_ggufs_cache["value"] = list(out)
    return out


def _gguf_matches_model_id(path: Path, model_id: str) -> bool:
    """True when *path* is a plausible GGUF for the catalog *model_id*.

    Sticky model_path must not win when the user selected a different catalog
    size (e.g. 7B path still set after switching to 14B).
    """

    from remedy.runtime.rmb.catalog import RMB_MODELS, catalog_id_from_hint

    mid = (model_id or "").strip()
    if not mid:
        return True
    # Free-form / unknown id — accept any existing path
    if mid not in RMB_MODELS:
        # Still try stem match via hint (status-bar style ids)
        hint_id = catalog_id_from_hint(mid)
        if not hint_id:
            return True
        mid = hint_id

    name = path.name.lower()
    spec = get_model_spec(mid)
    fn = spec.filename.lower()
    if name == fn or name.replace(".gguf", "") == fn.replace(".gguf", ""):
        return True
    # Path's own catalog id (from filename) matches
    path_id = catalog_id_from_hint(path.name)
    if path_id and path_id == mid:
        return True
    size = (spec.size_label or "").lower()
    if size and not re.search(rf"(?:^|[^0-9]){re.escape(size)}(?:[^0-9]|$)", name, re.I):
        return False
    wants_coder = "coder" in mid or "coder" in fn
    has_coder = "coder" in name
    if wants_coder and not has_coder:
        return False
    if not wants_coder and has_coder and "coder" not in mid:
        # instruct catalog vs coder file
        return False
    return bool(size)


def _resolve_model_path(
    state: dict[str, Any],
    home_dir: str | Path | None,
    *,
    trust_sticky_path: bool = False,
) -> Path | None:
    """Resolve any GGUF for RMB — catalog-aware sticky path, then scan.

    *trust_sticky_path*: when True (user just picked an explicit GGUF path),
    never replace a real on-disk path with a different catalog match. That was
    the “always loads Coder 7B again” bug — free-form Downloads files got
    re-resolved to the first 7B catalog file under models/.
    """
    from remedy.runtime.rmb.catalog import catalog_id_from_hint

    mid_raw = str(state.get("model_id") or DEFAULT_RMB_MODEL_ID)
    mid = catalog_id_from_hint(mid_raw) or mid_raw
    mp = str(state.get("model_path") or "").strip()
    # Explicit path always wins when the file exists and we trust the sticky
    # path (user selection) OR it matches the catalog model id.
    if mp and Path(mp).is_file():
        p = Path(mp)
        if trust_sticky_path:
            return p
        # Free-form model_id (stem) matching this file — compare against the
        # *path* identity only. Never include catalog mid in the tuple (that
        # made ``mid_raw in (…, mid)`` always true and stuck on wrong-size GGUF).
        stem = p.stem.lower()
        req = mid_raw.strip().lower()
        path_hint = (catalog_id_from_hint(p.name) or "").lower()
        if req and req in {stem, p.name.lower(), path_hint}:
            return p
        if mid and mid not in RMB_MODELS:
            # Non-catalog id → trust path only when stem-ish matches
            if req in stem or stem in req or path_hint == req:
                return p
            # else fall through to catalog scan
        elif _gguf_matches_model_id(p, mid or mid_raw):
            return p

    spec = get_model_spec(mid)
    size_tag = (spec.size_label or "7b").upper()  # e.g. 7B, 14B
    wants_coder = "coder" in mid.lower() or "coder" in spec.filename.lower()

    for root in _model_search_roots(home_dir):
        cand = root / spec.filename
        if cand.is_file():
            return cand
        if not root.is_dir():
            continue
        # Prefer exact-ish catalog tokens (size label, not hardcoded 7B)
        if wants_coder:
            for p in root.glob(f"*Coder*{size_tag}*.gguf"):
                if p.is_file() and _gguf_matches_model_id(p, mid):
                    return p
        for p in root.glob(f"*{size_tag}*Q4_K_M*.gguf"):
            if p.is_file() and _gguf_matches_model_id(p, mid):
                return p
        for p in root.glob(f"*{size_tag}*.gguf"):
            if p.is_file() and _gguf_matches_model_id(p, mid):
                return p
        # Normalize qwen25 → qwen2.5 style fragments
        frag = mid.replace("_", "-").lower().replace("qwen25", "qwen2.5")
        for p in root.glob("*.gguf"):
            n = p.name.lower().replace("qwen2.5", "qwen25")
            if frag and frag.replace("qwen2.5", "qwen25") in n.replace("qwen2.5", "qwen25"):
                if _gguf_matches_model_id(p, mid):
                    return p

    # Single matching GGUF in rmb/models → use it (never wrong-size sticky fallback)
    try:
        rmb_models = sorted(models_dir(home_dir).glob("*.gguf"))
        matches = [p for p in rmb_models if p.is_file() and _gguf_matches_model_id(p, mid)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            for p in matches:
                if "coder" in p.name.lower() and wants_coder:
                    return p
            return matches[0]
        # Only when no catalog match: legacy single-file convenience
        if len(rmb_models) == 1 and rmb_models[0].is_file() and mid == DEFAULT_RMB_MODEL_ID:
            return rmb_models[0]
    except Exception:
        pass
    return None


def _nvidia_ok() -> bool:
    """Compat: True when a CUDA-class card is present. Prefer probe_gpus()."""
    try:
        from remedy.runtime.gpu_probe import probe_gpus

        return any(d.vendor == "nvidia" for d in probe_gpus().devices)
    except Exception:
        return False


def _gpu_present() -> bool:
    try:
        from remedy.runtime.gpu_probe import probe_gpus

        return bool(probe_gpus().devices)
    except Exception:
        return False


def _live_process_has_mtp_flags(state: dict[str, Any] | None = None) -> bool:
    """True when the running llama-server already has --spec-type draft-mtp.

    Avoids restarting a healthy host just because rmb.json lost host_auto
    after an API recycle (that restart causes mid-chat 503 Loading model).
    """
    st = state or {}
    # Prefer explicit state
    ha_raw = st.get("host_auto")
    ha: dict[str, Any] = ha_raw if isinstance(ha_raw, dict) else {}
    if ha.get("mtp_armed"):
        return True
    # Managed child cmdline
    proc = _proc
    if proc is not None and proc.poll() is None:
        try:
            # Popen.args may be list
            args = proc.args
            if isinstance(args, (list, tuple)):
                joined = " ".join(str(a) for a in args).lower()
            else:
                joined = str(args or "").lower()
            if "draft-mtp" in joined or "--spec-type" in joined:
                return True
        except Exception:
            pass
    # Windows: query by port listener PID
    try:
        port = int(st.get("port") or DEFAULT_CHAT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_CHAT_PORT
    try:
        if os.name == "nt":
            import subprocess as _sp

            r = _sp.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen "
                        "-ErrorAction SilentlyContinue | Select-Object -First 1;"
                        "if($c){(Get-CimInstance Win32_Process -Filter "
                        "\"ProcessId=$($c.OwningProcess)\").CommandLine}"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            cmd = (r.stdout or "").lower()
            if "draft-mtp" in cmd or "--spec-type" in cmd:
                return True
    except Exception:
        pass
    return False


# --- GGUF host autoconfig (MTP / coding / template / thinking) ---
# Partner rule: user loads a GGUF; Remedy wires llama-server correctly.
# detect_gguf_host_profile / apply_host_profile_to_state live in host_profile.py.

# Cache binary capability probes (path + mtime → bool)
_spec_cap_cache: dict[str, tuple[float, bool]] = {}
_flag_cap_cache: dict[str, tuple[float, bool]] = {}


def binary_supports_draft_mtp(binary: Path | str | None) -> bool:
    """True when this llama-server build knows ``--spec-type draft-mtp``.

    Windows CUDA builds split flags into ``llama-common.dll`` / impl DLLs;
    we probe sibling binaries for the flag strings. Result is cached per
    path+mtime so Start stays fast.
    """
    if not binary:
        return False
    b = Path(binary)
    if not b.is_file():
        return False
    try:
        mtime = b.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = str(b.resolve()) if b.exists() else str(b)
    hit = _spec_cap_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]

    needles = (b"draft-mtp", b"--spec-type", b"spec-draft-n-max", b"LLAMA_ARG_SPEC_TYPE")
    found = False
    candidates: list[Path] = [b]
    parent = b.parent
    for pattern in (
        "llama-common*.dll",
        "llama-server*.dll",
        "llama-common.so*",
        "libcommon*",
        "llama-server",
    ):
        with contextlib.suppress(Exception):
            candidates.extend(parent.glob(pattern))
    seen: set[str] = set()
    for c in candidates:
        try:
            ck = str(c.resolve())
        except OSError:
            ck = str(c)
        if ck in seen or not c.is_file():
            continue
        seen.add(ck)
        try:
            # Cap read for huge CUDA libs — flags live in common/server, not ggml-cuda
            size = c.stat().st_size
            if size > 40 * 1024 * 1024:
                continue
            data = c.read_bytes()
        except OSError:
            continue
        if any(n in data for n in needles):
            found = True
            break

    _spec_cap_cache[key] = (mtime, found)
    return found


def binary_supports_cache_reuse(binary: Path | str | None) -> bool:
    """True when this llama-server build knows ``--cache-reuse``."""
    if not binary:
        return False
    b = Path(binary)
    if not b.is_file():
        return False
    try:
        mtime = b.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = f"cache-reuse|{b.resolve() if b.exists() else b}"
    hit = _flag_cap_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    found = False
    needles = (b"--cache-reuse", b"cache-reuse", b"LLAMA_ARG_CACHE_REUSE")
    candidates: list[Path] = [b]
    parent = b.parent
    for pattern in (
        "llama-common*.dll",
        "llama-server*.dll",
        "llama-common.so*",
        "libcommon*",
        "llama-server",
    ):
        with contextlib.suppress(Exception):
            candidates.extend(parent.glob(pattern))
    seen: set[str] = set()
    for c in candidates:
        try:
            ck = str(c.resolve())
        except OSError:
            ck = str(c)
        if ck in seen or not c.is_file():
            continue
        seen.add(ck)
        try:
            size = c.stat().st_size
            if size > 40 * 1024 * 1024:
                continue
            data = c.read_bytes()
        except OSError:
            continue
        if any(n in data for n in needles):
            found = True
            break
    _flag_cap_cache[key] = (mtime, found)
    return found


def binary_supports_chat_template_kwargs(binary: Path | str | None) -> bool:
    """True when this llama-server build knows ``--chat-template-kwargs``."""
    return _binary_has_needles(
        binary,
        "chat-template-kwargs",
        (b"--chat-template-kwargs", b"chat-template-kwargs", b"LLAMA_ARG_CHAT_TEMPLATE_KWARGS"),
    )


def binary_supports_n_cpu_moe(binary: Path | str | None) -> bool:
    """True when this llama-server build knows ``--n-cpu-moe``."""
    return _binary_has_needles(
        binary,
        "n-cpu-moe",
        (b"--n-cpu-moe", b"n-cpu-moe", b"LLAMA_ARG_N_CPU_MOE"),
    )


def binary_supports_reasoning_format(binary: Path | str | None) -> bool:
    """True when this llama-server build knows ``--reasoning-format``."""
    return _binary_has_needles(
        binary,
        "reasoning-format",
        (b"--reasoning-format", b"reasoning-format", b"LLAMA_ARG_THINK"),
    )


def binary_supports_reasoning_budget(binary: Path | str | None) -> bool:
    """True when this llama-server build knows ``--reasoning-budget``."""
    return _binary_has_needles(
        binary,
        "reasoning-budget",
        (b"--reasoning-budget", b"reasoning-budget", b"LLAMA_ARG_REASONING_BUDGET"),
    )


def binary_supports_reasoning_off(binary: Path | str | None) -> bool:
    """True when this llama-server build knows ``--reasoning off``."""
    return _binary_has_needles(
        binary,
        "reasoning-off",
        (b"--reasoning off", b"reasoning off"),
    )


def _binary_has_needles(
    binary: Path | str | None, cache_key: str, needles: tuple[bytes, ...]
) -> bool:
    if not binary:
        return False
    b = Path(binary)
    if not b.is_file():
        return False
    try:
        mtime = b.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = f"{cache_key}|{b.resolve() if b.exists() else b}"
    hit = _flag_cap_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    found = False
    candidates: list[Path] = [b]
    parent = b.parent
    for pattern in (
        "llama-common*.dll",
        "llama-server*.dll",
        "llama-common.so*",
        "libcommon*",
        "llama-server",
    ):
        with contextlib.suppress(Exception):
            candidates.extend(parent.glob(pattern))
    seen: set[str] = set()
    for c in candidates:
        try:
            ck = str(c.resolve())
        except OSError:
            ck = str(c)
        if ck in seen or not c.is_file():
            continue
        seen.add(ck)
        try:
            size = c.stat().st_size
            if size > 40 * 1024 * 1024:
                continue
            data = c.read_bytes()
        except OSError:
            continue
        if any(n in data for n in needles):
            found = True
            break
    _flag_cap_cache[key] = (mtime, found)
    return found


def _engine_kwargs(state: dict[str, Any]) -> dict[str, Any]:
    """Pull inference-engine knobs out of RMB state (skip unset/empty).

    Returned dict is splatted into ``_build_cmd``; every key is optional there.
    """
    out: dict[str, Any] = {}
    for key, cast in (
        ("temperature", float),
        ("top_p", float),
        ("top_k", int),
        ("min_p", float),
        ("repeat_penalty", float),
        ("repeat_last_n", int),
        ("seed", int),
        ("batch_size", int),
        ("ubatch_size", int),
        ("rope_freq_scale", float),
        ("rope_freq_base", float),
        # --- KoboldCpp-class parity knobs ---
        ("typical_p", float),
        ("tfs_z", float),
        ("mirostat", int),
        ("mirostat_tau", float),
        ("mirostat_eta", float),
        ("presence_penalty", float),
        ("frequency_penalty", float),
        ("main_gpu", int),
        ("threads_batch", int),
        ("yarn_orig_ctx", int),
        ("yarn_factor", float),
        ("yarn_beta_fast", float),
        ("yarn_beta_slow", float),
        # --- DRY + XTC samplers (KoboldCpp parity) ---
        ("dry_multiplier", float),
        ("dry_base", float),
        ("dry_allowed_length", int),
        ("dry_penalty_last_n", int),
        ("xtc_probability", float),
        ("xtc_threshold", float),
        ("cache_reuse", int),
    ):
        val = state.get(key)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        try:
            out[key] = cast(val)
        except (TypeError, ValueError):
            continue
    for key in ("mmproj", "chat_template", "cache_type", "tensor_split", "samplers", "rope_scaling"):
        val = str(state.get(key) or "").strip()
        if val:
            out[key] = val
    out["use_jinja"] = bool(state.get("use_jinja", True))
    out["mlock"] = bool(state.get("mlock", False))
    out["no_mmap"] = bool(state.get("no_mmap", False))
    out["no_kv_offload"] = bool(state.get("no_kv_offload", False))
    return out


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
    host_profile: dict[str, Any] | None = None,
    enable_mtp: bool | None = None,
    # --- inference engine knobs (all optional; skipped when unset) ---
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    min_p: float | None = None,
    repeat_penalty: float | None = None,
    repeat_last_n: int | None = None,
    seed: int | None = None,
    batch_size: int | None = None,
    ubatch_size: int | None = None,
    mmproj: str | None = None,
    chat_template: str | None = None,
    use_jinja: bool = True,
    rope_freq_scale: float | None = None,
    rope_freq_base: float | None = None,
    mlock: bool = False,
    no_mmap: bool = False,
    cache_type: str | None = None,
    # --- KoboldCpp-class parity knobs (all optional; skipped when unset) ---
    typical_p: float | None = None,
    tfs_z: float | None = None,
    mirostat: int | None = None,
    mirostat_tau: float | None = None,
    mirostat_eta: float | None = None,
    presence_penalty: float | None = None,
    frequency_penalty: float | None = None,
    main_gpu: int | None = None,
    threads_batch: int | None = None,
    tensor_split: str | None = None,
    samplers: str | None = None,
    rope_scaling: str | None = None,
    yarn_orig_ctx: int | None = None,
    yarn_factor: float | None = None,
    yarn_beta_fast: float | None = None,
    yarn_beta_slow: float | None = None,
    no_kv_offload: bool = False,
    # --- DRY + XTC samplers (KoboldCpp parity) ---
    dry_multiplier: float | None = None,
    dry_base: float | None = None,
    dry_allowed_length: int | None = None,
    dry_penalty_last_n: int | None = None,
    xtc_probability: float | None = None,
    xtc_threshold: float | None = None,
    cache_reuse: int | None = None,
) -> list[str]:
    """Build llama-server argv. Auto-enables MTP speculative flags when the
    GGUF looks like MTP **and** the binary supports draft-mtp.

    ``enable_mtp=False`` forces a plain start (soft-retry after bad flags).
    """
    profile = host_profile if host_profile is not None else detect_gguf_host_profile(model)
    use_parallel = int(parallel)
    if profile.get("force_parallel_1"):
        use_parallel = 1

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
        str(max(1, use_parallel)),
        "--cont-batching",
    ]
    if use_jinja or profile.get("use_jinja"):
        cmd.append("--jinja")
    # Qwen thinking-off: prefer --reasoning off (current llama.cpp). The
    # chat-template-kwargs enable_thinking path is deprecated and noisy.
    reasoning_off = bool(profile.get("qwen_thinking_toggle")) and not profile.get(
        "always_think"
    )
    if reasoning_off and binary_supports_reasoning_off(binary):
        cmd.extend(["--reasoning", "off"])
    else:
        kwargs_json = str(profile.get("chat_template_kwargs") or "").strip()
        if kwargs_json and binary_supports_chat_template_kwargs(binary):
            cmd.extend(["--chat-template-kwargs", kwargs_json])
    # MoE experts on the CPU. Only the active experts are read per token, so a
    # 3B-active model keeps near-dense speed while its full weights live in
    # system RAM - that is what lets a 35B class model run on a 12GB card
    # using ~4GB of VRAM. Dense models leave this at 0 and are unaffected.
    try:
        _moe = int(profile.get("n_cpu_moe") or 0)
    except (TypeError, ValueError):
        _moe = 0
    if _moe <= 0:
        # Not every caller passes a host profile, so fall back to the catalog
        # entry for this GGUF. Without this a known MoE model would silently
        # load every expert onto the GPU and fail to fit.
        try:
            from remedy.runtime.rmb.catalog import (
                catalog_id_from_hint,
                get_model_spec,
            )

            _cid = catalog_id_from_hint(model.name)
            if _cid:
                _moe = int(getattr(get_model_spec(_cid), "n_cpu_moe", 0) or 0)
        except Exception:
            _moe = 0
    if _moe > 0 and binary_supports_n_cpu_moe(binary):
        cmd.extend(["--n-cpu-moe", str(_moe)])

    # Separate thinking from the answer at the source. ``deepseek`` puts
    # thoughts in ``message.reasoning_content``, which Remedy already consumes
    # as its thinking channel - so the scratchpad reaches the thinking pane
    # instead of leaking into the reply as raw <think> tags, and none of it is
    # thrown away. Reasoning models (and distills, which open every answer with
    # a <think> block) are unusable without this.
    if binary_supports_reasoning_format(binary):
        cmd.extend(["--reasoning-format", "deepseek"])
    if profile.get("reasoning_budget") is not None and binary_supports_reasoning_budget(
        binary
    ):
        try:
            budget = int(profile.get("reasoning_budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        cmd.extend(["--reasoning-budget", str(budget)])
    if threads and int(threads) > 0:
        cmd.extend(["--threads", str(int(threads))])
    if flash_attn:
        cmd.extend(["-fa", "on"])
    if batch_size and int(batch_size) > 0:
        cmd.extend(["--batch-size", str(int(batch_size))])
    if ubatch_size and int(ubatch_size) > 0:
        cmd.extend(["--ubatch-size", str(int(ubatch_size))])
    if mmproj and str(mmproj).strip():
        cmd.extend(["--mmproj", str(mmproj).strip()])
    if chat_template and str(chat_template).strip():
        cmd.extend(["--chat-template", str(chat_template).strip()])
    if temperature is not None and float(temperature) >= 0:
        cmd.extend(["--temp", str(float(temperature))])
    if top_k is not None and int(top_k) > 0:
        cmd.extend(["--top-k", str(int(top_k))])
    if top_p is not None and 0 < float(top_p) <= 1:
        cmd.extend(["--top-p", str(float(top_p))])
    if min_p is not None and 0 <= float(min_p) < 1:
        cmd.extend(["--min-p", str(float(min_p))])
    if repeat_penalty is not None and float(repeat_penalty) > 0:
        cmd.extend(["--repeat-penalty", str(float(repeat_penalty))])
    if repeat_last_n is not None and int(repeat_last_n) >= 0:
        cmd.extend(["--repeat-last-n", str(int(repeat_last_n))])
    if seed is not None and int(seed) >= 0:
        cmd.extend(["--seed", str(int(seed))])
    if rope_freq_scale is not None and float(rope_freq_scale) > 0:
        cmd.extend(["--rope-freq-scale", str(float(rope_freq_scale))])
    if rope_freq_base is not None and float(rope_freq_base) > 0:
        cmd.extend(["--rope-freq-base", str(float(rope_freq_base))])
    if mlock:
        cmd.append("--mlock")
    if no_mmap:
        cmd.append("--no-mmap")
    if cache_type and str(cache_type).strip():
        ct = str(cache_type).strip()
        cmd.extend(["--cache-type-k", ct, "--cache-type-v", ct])
    if no_kv_offload:
        cmd.append("--no-kv-offload")
    if typical_p is not None and 0 < float(typical_p) <= 1:
        cmd.extend(["--typical", str(float(typical_p))])
    if tfs_z is not None and 0 < float(tfs_z) <= 1:
        cmd.extend(["--tfs", str(float(tfs_z))])
    miro_v = int(mirostat) if mirostat is not None else 0
    if mirostat is not None and miro_v in (0, 1, 2):
        cmd.extend(["--mirostat", str(miro_v)])
    if miro_v in (1, 2):
        if mirostat_tau is not None and float(mirostat_tau) > 0:
            cmd.extend(["--mirostat-tau", str(float(mirostat_tau))])
        if mirostat_eta is not None and float(mirostat_eta) > 0:
            cmd.extend(["--mirostat-eta", str(float(mirostat_eta))])
    # presence/frequency accept negative values too (KoboldCpp parity)
    if presence_penalty is not None and float(presence_penalty) != 0:
        cmd.extend(["--presence-penalty", str(float(presence_penalty))])
    if frequency_penalty is not None and float(frequency_penalty) != 0:
        cmd.extend(["--frequency-penalty", str(float(frequency_penalty))])
    # DRY sampling (KoboldCpp parity; --dry-multiplier 0 = off)
    if dry_multiplier is not None and float(dry_multiplier) != 0:
        cmd.extend(["--dry-multiplier", str(float(dry_multiplier))])
        if dry_base is not None and float(dry_base) > 0:
            cmd.extend(["--dry-base", str(float(dry_base))])
        if dry_allowed_length is not None and int(dry_allowed_length) > 0:
            cmd.extend(["--dry-allowed-length", str(int(dry_allowed_length))])
        if dry_penalty_last_n is not None and int(dry_penalty_last_n) != 0:
            cmd.extend(["--dry-penalty-last-n", str(int(dry_penalty_last_n))])
    # XTC (KoboldCpp parity; probability 0 = off)
    if xtc_probability is not None and 0 < float(xtc_probability) <= 1:
        cmd.extend(["--xtc-probability", str(float(xtc_probability))])
        if xtc_threshold is not None and 0 < float(xtc_threshold) < 1:
            cmd.extend(["--xtc-threshold", str(float(xtc_threshold))])
    if main_gpu is not None and int(main_gpu) >= 0:
        cmd.extend(["--main-gpu", str(int(main_gpu))])
    if threads_batch is not None and int(threads_batch) > 0:
        cmd.extend(["--threads-batch", str(int(threads_batch))])
    if tensor_split and str(tensor_split).strip():
        cmd.extend(["--tensor-split", str(tensor_split).strip()])
    if samplers and str(samplers).strip():
        cmd.extend(["--samplers", str(samplers).strip()])
    if rope_scaling and str(rope_scaling).strip().lower() in ("linear", "yarn"):
        cmd.extend(["--rope-scaling", str(rope_scaling).strip().lower()])
        if yarn_orig_ctx is not None and int(yarn_orig_ctx) > 0:
            cmd.extend(["--yarn-orig-ctx", str(int(yarn_orig_ctx))])
        if yarn_factor is not None and float(yarn_factor) > 0:
            cmd.extend(["--yarn-factor", str(float(yarn_factor))])
        if yarn_beta_fast is not None and float(yarn_beta_fast) > 0:
            cmd.extend(["--yarn-beta-fast", str(float(yarn_beta_fast))])
        if yarn_beta_slow is not None and float(yarn_beta_slow) > 0:
            cmd.extend(["--yarn-beta-slow", str(float(yarn_beta_slow))])

    # MTP: baked-in heads, or a sibling mtp-<stem>.gguf via --model-draft
    want_mtp = bool(profile.get("mtp")) if enable_mtp is None else bool(enable_mtp)
    if want_mtp and profile.get("mtp"):
        if binary_supports_draft_mtp(binary):
            spec = str(profile.get("spec_type") or "draft-mtp")
            n_max = int(profile.get("spec_draft_n_max") or 2)
            n_max = max(1, min(8, n_max))
            cmd.extend(
                [
                    "--spec-type",
                    spec,
                    "--spec-draft-n-max",
                    str(n_max),
                ]
            )
            draft = str(profile.get("model_draft") or "").strip()
            if draft:
                draft_p = Path(draft)
                try:
                    same = draft_p.resolve() == Path(model).resolve()
                except OSError:
                    same = str(draft_p) == str(model)
                if draft_p.is_file() and not same:
                    cmd.extend(["--model-draft", str(draft_p)])
                    try:
                        raw_dn = profile.get("n_gpu_layers_draft")
                        if raw_dn is None:
                            # Keep draft small so it does not crowd the main ngl.
                            ngl_i = int(ngl)
                            draft_ngl = (
                                max(4, min(16, ngl_i // 4)) if ngl_i > 0 else 8
                            )
                        else:
                            draft_ngl = max(1, min(99, int(raw_dn)))
                    except (TypeError, ValueError):
                        draft_ngl = 8
                    cmd.extend(["--n-gpu-layers-draft", str(draft_ngl)])
            logger.info(
                "RMB autoconfig: MTP enabled (%s n_max=%s draft=%s) for %s",
                spec,
                n_max,
                Path(draft).name if draft else "heads",
                model.name,
            )
        else:
            logger.warning(
                "RMB autoconfig: GGUF looks like MTP (%s) but llama-server "
                "build lacks draft-mtp — starting without speculative flags. "
                "Update the CUDA/CPU runtime to unlock ~2x decode.",
                model.name,
            )
    # Prefix cache: ReAct tool loops resubmit the same system head every step.
    if cache_reuse is not None and int(cache_reuse) > 0 and binary_supports_cache_reuse(binary):
        cmd.extend(["--cache-reuse", str(int(cache_reuse))])
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
        from remedy.runtime.rmb.mode import is_local_agent_mode
        from remedy.vision.config import vision_section_from_config
        from remedy.vision.service import ensure_server

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


def wait_rmb_ready(
    home_dir: str | Path | None = None,
    *,
    timeout_s: float = 120.0,
    poll_s: float = 0.5,
) -> dict[str, Any]:
    """Block until RMB is healthy or *timeout_s* elapses.

    Partner path for HTTP 503 / WinError 64 / connection refused: restart the
    host if needed and wait for weights (typical 3–30s on GPU). Mid-turn
    recovery must not depend on the user.
    """
    deadline = time.time() + max(1.0, float(timeout_s))
    kicked_async = False
    kicked_sync = False
    cleared_stall = False
    ensure_rmb_watchdog(home_dir)
    while time.time() < deadline:
        if is_running(home_dir, force=True, require_http=True):
            mark_used()
            return {"ok": True, "ready": True}
        if _refresh_user_stopped(home_dir):
            return {
                "ok": False,
                "ready": False,
                "error": "RMB was stopped by user",
            }
        # Mid-load is OK — just wait; only force-clear when stalled
        if is_loading(home_dir) and not loading_stalled(home_dir, max_s=min(150.0, timeout_s)):
            time.sleep(max(0.2, float(poll_s)))
            continue
        if loading_stalled(home_dir, max_s=min(150.0, timeout_s)) and not cleared_stall:
            cleared_stall = True
            logger.warning("RMB wait_rmb_ready: loading stall — force stop + restart")
            with contextlib.suppress(Exception):
                stop_rmb_server(
                    home_dir=home_dir, resume_vision=False, user_intent=False
                )
            kicked_sync = False  # allow a fresh sync start
        alive = managed_process_alive() or is_starting() or is_loading(home_dir)
        if not alive:
            remaining = deadline - time.time()
            if not kicked_sync and remaining > 10:
                # Sync start once — more reliable than fire-and-forget mid-turn
                kicked_sync = True
                try:
                    r = start_rmb_server(
                        home_dir=home_dir,
                        wait_s=min(100.0, max(20.0, remaining - 2)),
                    )
                    if r.get("ok") and is_running(
                        home_dir, force=True, require_http=True
                    ):
                        mark_used()
                        return {"ok": True, "ready": True, "restarted": True}
                    # Start claimed ok but still loading — keep waiting
                    if r.get("ok") or r.get("starting"):
                        continue
                except Exception:
                    logger.exception("RMB wait_rmb_ready sync start failed")
            elif not kicked_async:
                wake_rmb_async(home_dir)
                kicked_async = True
        time.sleep(max(0.2, float(poll_s)))
    return {
        "ok": False,
        "ready": False,
        "error": f"RMB not ready within {timeout_s:.0f}s",
        "starting": is_starting() or managed_process_alive() or is_loading(home_dir),
        "detail": _last_health_detail,
        "last_error": _last_start_error,
    }


def wake_rmb_async(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Start RMB in a daemon thread if needed (never blocks the chat path)."""
    ensure_rmb_watchdog(home_dir)
    # Adopt orphan host from previous API process before spawning a second one
    with contextlib.suppress(Exception):
        ad = adopt_existing_host(home_dir)
        if ad.get("ok") and (ad.get("adopted") or is_running(home_dir, force=True, require_http=True)):
            mark_used()
            return {"ok": True, "already_running": True, "adopted": bool(ad.get("adopted"))}
    if is_running(home_dir, force=True, require_http=True):
        mark_used()
        return {"ok": True, "already_running": True}
    if is_starting() or managed_process_alive():
        return {"ok": True, "starting": True}
    if is_loading(home_dir):
        return {"ok": True, "starting": True, "loading": True}
    if _refresh_user_stopped(home_dir):
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
    clear_user_stopped: bool = False,
) -> dict[str, Any]:
    """Start RMB chat llama-server if not healthy.

    Unloads SmolVLM first. On failure, clears vision_suspended.
    Health wait does **not** hold the process lock so Stop can interrupt.

    Never returns ``already_running`` when the disk model_path differs from
    what we would spawn — callers that changed GGUF must get a real restart.

    Concurrent callers single-flight: one spawn, others wait for the same result.

    ``clear_user_stopped`` is only for explicit user Start / settings apply.
    Lifespan, watchdog, and wake must not wipe the persisted stay-off bit.
    """
    global _proc, _atexit_registered, _user_stopped
    global _start_flight_active, _start_flight_result, _last_start_error

    if clear_user_stopped:
        _persist_user_stopped(home_dir, False)
    elif _refresh_user_stopped(home_dir):
        return {"ok": False, "error": "RMB stopped by user"}

    ensure_rmb_watchdog(home_dir)

    # Single-flight: if another thread is already starting, join its wait
    with _start_flight_lock:
        if _start_flight_active:
            join = True
        else:
            join = False
            _start_flight_active = True
            _start_flight_result = None
            _start_flight_event.clear()

    if join:
        logger.info("RMB: joining in-flight start (single-flight)")
        # Wait a bit longer than the outer wait so the leader finishes first
        _start_flight_event.wait(timeout=max(30.0, float(wait_s) + 30.0))
        if is_running(home_dir, force=True, require_http=True):
            mark_used()
            return {
                "ok": True,
                "already_running": True,
                "joined_flight": True,
                "base_url": merge_state(load_rmb_json(home_dir)).get("base_url"),
            }
        if isinstance(_start_flight_result, dict):
            return dict(_start_flight_result)
        return {
            "ok": is_running(home_dir, force=True, require_http=True),
            "joined_flight": True,
            "starting": is_starting() or is_loading(home_dir),
        }

    result: dict[str, Any] = {"ok": False, "error": "start failed"}
    try:
        result = _start_rmb_server_impl(home_dir=home_dir, wait_s=wait_s)
    except Exception as exc:
        logger.exception("RMB start_rmb_server crashed")
        result = {"ok": False, "error": f"start crashed: {exc}"}
    finally:
        with _start_flight_lock:
            _start_flight_result = dict(result)
            if not result.get("ok"):
                _last_start_error = str(result.get("error") or "start failed")
            elif result.get("ok"):
                _last_start_error = None
            _start_flight_active = False
            _start_flight_event.set()
    return result


def _start_rmb_server_impl(
    *,
    home_dir: str | Path | None = None,
    wait_s: float = 90.0,
) -> dict[str, Any]:
    """Inner start (single-flight leader only)."""
    global _proc, _atexit_registered, _user_stopped, _last_start_error

    # Desired GGUF on disk (after settings write)
    st_probe = merge_state(load_rmb_json(home_dir))
    want_model = _resolve_model_path(st_probe, home_dir, trust_sticky_path=True)
    want_name = want_model.name.lower() if want_model else ""

    # Fast path only if healthy AND already the desired GGUF (check via /v1/models id)
    if is_running(home_dir, force=True, require_http=True):
        same = True
        if want_name:
            try:
                from urllib.request import urlopen

                base = str(
                    st_probe.get("base_url") or f"http://{DEFAULT_HOST}:{DEFAULT_CHAT_PORT}/v1"
                ).rstrip("/")
                with urlopen(base + "/models", timeout=1.5) as resp:  # noqa: S310
                    import json as _json

                    data = _json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
                ids = []
                for row in data.get("data") or data.get("models") or []:
                    if isinstance(row, dict):
                        ids.append(str(row.get("id") or row.get("model") or "").lower())
                # ids often contain full path or filename
                same = any(want_name in i or Path(i).name.lower() == want_name for i in ids) if ids else False
            except Exception:
                same = False
        if same:
            # Upgrade path: MTP GGUF running without speculative flags after
            # autoconfig was added/improved → restart so partner gets full speed.
            # Do NOT restart when the live process already has draft-mtp (host_auto
            # may be missing after API recycle) — mid-chat restart → 503 Loading.
            want_profile = detect_gguf_host_profile(want_model)
            prev_auto_raw = st_probe.get("host_auto")
            prev_auto: dict[str, Any] = (
                prev_auto_raw if isinstance(prev_auto_raw, dict) else {}
            )
            live_mtp = _live_process_has_mtp_flags(st_probe)
            if live_mtp and not prev_auto.get("mtp_armed"):
                # Heal rmb.json without bounce
                prev_auto = {
                    **want_profile,
                    **prev_auto,
                    "mtp_armed": True,
                    "binary_supports_mtp": True,
                }
                st_probe["host_auto"] = prev_auto
                with contextlib.suppress(Exception):
                    save_rmb_json(st_probe, home_dir)
            needs_mtp_upgrade = bool(
                want_profile.get("mtp")
                and binary_supports_draft_mtp(st_probe.get("runtime_binary") or "")
                and not prev_auto.get("mtp_armed")
                and not prev_auto.get("mtp_soft_disabled")
                and not live_mtp
            )
            if needs_mtp_upgrade:
                logger.info(
                    "RMB autoconfig: restarting to arm MTP for %s",
                    want_name or "?",
                )
                stop_rmb_server(
                    home_dir=home_dir, resume_vision=False, user_intent=False
                )
                _user_stopped = False
            else:
                mark_used()
                _set_vision_suspended(home_dir, True)
                with contextlib.suppress(Exception):
                    sync_context_window_cache(st_probe)
                # Always re-align chat identity to the live GGUF (status bar / settings)
                chat_sync = sync_rmb_chat_identity(
                    st_probe, home_dir=home_dir, force_provider=False
                )
                return {
                    "ok": True,
                    "already_running": True,
                    "base_url": st_probe.get("base_url"),
                    "ctx_size": int(st_probe.get("ctx_size") or 0) or None,
                    "model_path": str(want_model)
                    if want_model
                    else st_probe.get("model_path"),
                    "chat_sync": chat_sync,
                    "chat_model": chat_sync.get("stem"),
                    "vision_suspended": True,
                    "host_auto": prev_auto or want_profile,
                }
        else:
            # Wrong GGUF still serving — tear down before spawn
            logger.info(
                "RMB: healthy host holds wrong GGUF (want %s); force stop before start",
                want_name or "?",
            )
            stop_rmb_server(home_dir=home_dir, resume_vision=False, user_intent=False)
            _user_stopped = False

    # Outside lock: mid-load wait / clear wedged occupant (Stop must not block on this)
    host_probe = str(st_probe.get("host") or DEFAULT_HOST)
    try:
        port_probe = int(st_probe.get("port") or DEFAULT_CHAT_PORT)
    except (TypeError, ValueError):
        port_probe = DEFAULT_CHAT_PORT
    base_probe = str(
        st_probe.get("base_url") or f"http://{host_probe}:{port_probe}/v1"
    )
    if (
        not is_running(home_dir, force=True, require_http=True)
        and _port_open(host_probe, port_probe)
        and not _health(base_probe)
    ):
        resolved = _resolve_occupied_port(
            host_probe, port_probe, base_probe, wait_s=min(float(wait_s), 120.0)
        )
        if resolved.get("already_healthy") or resolved.get("became_healthy"):
            mark_used()
            with contextlib.suppress(Exception):
                adopt_existing_host(home_dir)
            chat_sync = {}
            with contextlib.suppress(Exception):
                chat_sync = sync_rmb_chat_identity(
                    merge_state(load_rmb_json(home_dir)),
                    home_dir=home_dir,
                    force_provider=False,
                )
            return {
                "ok": True,
                "already_running": True,
                "adopted": True,
                "base_url": base_probe,
                "model_path": str(want_model) if want_model else st_probe.get("model_path"),
                "chat_sync": chat_sync,
                "vision_suspended": True,
                "waited_for_load": bool(resolved.get("became_healthy")),
            }
        if not resolved.get("ok"):
            _last_start_error = str(
                resolved.get("error") or f"Port {port_probe} occupied"
            )
            return {
                "ok": False,
                "error": _last_start_error,
                "port": port_probe,
                "pid": resolved.get("pid"),
                "vision_suspended": False,
            }

    with _lock:
        if is_running(home_dir, force=True, require_http=True):
            # Still up after force stop attempt — kill port again inside lock
            try:
                port_k = int(st_probe.get("port") or DEFAULT_CHAT_PORT)
            except (TypeError, ValueError):
                port_k = DEFAULT_CHAT_PORT
            _kill_listeners_on_port(port_k)
            if _proc is not None:
                with contextlib.suppress(Exception):
                    _proc.kill()
                _proc = None
            invalidate_cache()
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
            if payload.get("error"):
                global _last_start_error
                _last_start_error = str(payload.get("error"))
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

        # Trust sticky path from settings — never re-pick catalog 7B over user choice
        model = _resolve_model_path(state, home_dir, trust_sticky_path=True)
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

        # Quick re-check under lock (no long wait — that happens outside)
        if _port_open(host, port) and not _health(base):
            _kill_listeners_on_port(port)
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

        # Autofit (default): measure VRAM/RAM + GGUF and pick a window that
        # actually loads. Locked / turbo / quality keep the user's knobs.
        hw = probe_hardware()
        host_profile = detect_gguf_host_profile(
            model, hardware=hw.to_public() if hw is not None else None
        )
        apply_host_profile_to_state(state, host_profile)
        last_good = (
            state.get("last_good_fit")
            if isinstance(state.get("last_good_fit"), dict)
            else None
        )
        if should_autofit(state):
            plan = plan_autofit(model, hardware=hw, last_good=last_good)
            apply_plan_to_state(state, plan)
            logger.info("RMB autofit: %s", plan.summary())
        else:
            plan = plan_from_state(state, model, hardware=hw)
            if plan.n_gpu_layers < 0 and not hw.usable_gpu:
                from dataclasses import replace as _replace

                plan = _replace(plan, n_gpu_layers=0, flash_attn=False)
            apply_plan_to_state(state, plan)
        # Don't push GPU layers onto a runtime that cannot drive this card.
        if plan.n_gpu_layers != 0:
            try:
                from dataclasses import replace as _replace

                from remedy.runtime.gpu_probe import runtime_matches_gpu

                vendor = hw.gpu_vendor or ("nvidia" if hw.nvidia else "")
                rid = str(state.get("runtime_id") or "")
                if vendor and not runtime_matches_gpu(
                    vendor, runtime_id=rid, binary=binary
                ):
                    plan = _replace(plan, n_gpu_layers=0, flash_attn=False)
                    apply_plan_to_state(state, plan)
                    logger.info(
                        "RMB: GPU present but this runtime cannot offload it — CPU layers"
                    )
            except Exception:
                pass
        ctx = int(plan.ctx_size)
        ngl = int(plan.n_gpu_layers)

        # Autoconfig from GGUF — jinja / thinking-off / mmap / MTP (no user knobs)
        use_parallel = int(state.get("parallel") or 1)
        if host_profile.get("force_parallel_1"):
            use_parallel = 1
            state["parallel"] = 1

        cmd = _build_cmd(
            binary,
            model,
            host=host,
            port=port,
            ctx=ctx,
            ngl=ngl,
            threads=int(state.get("threads") or 0),
            parallel=use_parallel,
            flash_attn=bool(state.get("flash_attn", True)),
            host_profile=host_profile,
            **_engine_kwargs(state),
        )
        mtp_armed = (
            bool(host_profile.get("mtp"))
            and "--spec-type" in cmd
            and binary_supports_draft_mtp(binary)
        )
        host_auto = {
            **host_profile,
            "mtp_armed": mtp_armed,
            "binary_supports_mtp": binary_supports_draft_mtp(binary),
            "cmd_flags": [a for a in cmd if a.startswith("--") or a in ("-fa", "-ngl", "-m")],
        }

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

        def _spawn(argv: list[str]) -> subprocess.Popen[Any]:
            return subprocess.Popen(
                argv,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(binary.parent),
                creationflags=creation,
            )

        try:
            _proc = _spawn(cmd)
        except Exception as e:
            if log_f is not subprocess.DEVNULL:
                with contextlib.suppress(Exception):
                    log_f.close()
            return _fail({"ok": False, "error": f"failed to spawn llama-server: {e}"})

        # Soft-retry: if MTP flags make an older binary die instantly, restart plain.
        # Partner rule — never fail to load a valid GGUF because of speculative knobs.
        if mtp_armed:
            time.sleep(0.35)
            if _proc.poll() is not None:
                logger.warning(
                    "RMB autoconfig: MTP spawn exited code %s — retrying without "
                    "speculative flags so the model still loads",
                    _proc.returncode,
                )
                cmd = _build_cmd(
                    binary,
                    model,
                    host=host,
                    port=port,
                    ctx=ctx,
                    ngl=ngl,
                    threads=int(state.get("threads") or 0),
                    parallel=use_parallel,
                    flash_attn=bool(state.get("flash_attn", True)),
                    host_profile=host_profile,
                    enable_mtp=False,
                    **_engine_kwargs(state),
                )
                host_auto["mtp_armed"] = False
                host_auto["mtp_soft_disabled"] = True
                host_auto["mtp_soft_reason"] = (
                    f"speculative flags rejected (exit {_proc.returncode})"
                )
                try:
                    _proc = _spawn(cmd)
                except Exception as e:
                    if log_f is not subprocess.DEVNULL:
                        with contextlib.suppress(Exception):
                            log_f.close()
                    return _fail(
                        {"ok": False, "error": f"failed to spawn llama-server: {e}"}
                    )

        state["model_path"] = str(model)
        # Free-form stem as model_id so identity never snaps to catalog 7B
        with contextlib.suppress(Exception):
            state["model_id"] = model.stem
        state["runtime_binary"] = str(binary)
        state["pid"] = _proc.pid
        state["enabled"] = True
        state["vision_suspended"] = True
        state["base_url"] = base
        state["port"] = port
        state["host_auto"] = host_auto
        save_rmb_json(state, home_dir)
        invalidate_cache()
        # Auto chat identity: Provider + status bar follow this GGUF
        with contextlib.suppress(Exception):
            sync_rmb_chat_identity(state, home_dir=home_dir, force_provider=False)

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
        wait_host_auto = host_auto
        wait_plan = plan
        wait_hw = hw
        wait_use_parallel = use_parallel
        wait_host = host
        wait_port = port
        wait_home = home_dir

    # --- health wait OUTSIDE lock so Stop can run ---
    if wait_s <= 0:
        return {
            "ok": True,
            "starting": True,
            "base_url": wait_base,
            "pid": wait_pid,
            "vision_suspended": True,
            "vision": wait_vision,
            "host_auto": wait_host_auto,
            "autofit": wait_plan.to_public() if wait_plan else None,
        }

    def _finish_healthy() -> dict[str, Any]:
        mark_used()
        _clear_starting()
        _note_loading_state(False)
        live_ctx = wait_ctx
        with contextlib.suppress(Exception):
            probed = probe_live_n_ctx(wait_base)
            if probed and probed >= 2048:
                live_ctx = int(probed)
        with contextlib.suppress(Exception):
            from remedy.nanoswarm.token_nanobot import cache_context_window

            cache_context_window(wait_base, wait_mid, live_ctx)
            cache_context_window(wait_base, Path(wait_model).name, live_ctx)
        with contextlib.suppress(Exception):
            st_ok = merge_state(load_rmb_json(wait_home))
            if wait_plan is not None:
                st_ok["last_good_fit"] = last_good_payload(wait_plan, wait_model, wait_hw)
                st_ok["ctx_size"] = live_ctx
                apply_plan_to_state(st_ok, wait_plan)
                st_ok["ctx_size"] = live_ctx
                save_rmb_json(st_ok, wait_home)
        return {
            "ok": True,
            "base_url": wait_base,
            "pid": wait_pid,
            "model_path": wait_model,
            "binary": wait_binary,
            "ctx_size": live_ctx,
            "n_gpu_layers": wait_ngl,
            "log": wait_log,
            "vision_suspended": True,
            "vision": wait_vision,
            "host_auto": wait_host_auto,
            "autofit": wait_plan.to_public() if wait_plan else None,
        }

    # The wait/retry loop — early-exit OOM walks the fit down instead of dying.
    remaining = max(1.0, float(wait_s))
    retries = 0
    max_retries = 3
    while True:
        deadline = time.time() + remaining
        early_code: int | None = None
        timed_out = False
        while time.time() < deadline:
            if _user_stopped:
                return {
                    "ok": False,
                    "error": "RMB start cancelled (stopped)",
                    "vision_suspended": False,
                }
            if wait_proc.poll() is not None:
                early_code = wait_proc.returncode
                with _lock:
                    if _proc is wait_proc:
                        _proc = None
                break
            if is_running(home_dir, force=True, require_http=True):
                return _finish_healthy()
            _note_loading_state(True)
            time.sleep(0.4)
        else:
            timed_out = True
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

        tail = _tail_log(wait_log)
        kind = classify_start_failure(
            tail, exit_code=early_code, timed_out=timed_out
        )
        nxt = downgrade_plan(wait_plan, kind) if wait_plan is not None else None
        can_retry = (
            nxt is not None
            and retries < max_retries
            and not _user_stopped
            and (not timed_out or retries == 0)
        )
        if not can_retry:
            _clear_starting()
            _set_vision_suspended(home_dir, False)
            if log_f is not subprocess.DEVNULL:
                with contextlib.suppress(Exception):
                    log_f.close()
            if timed_out:
                _last_start_error = f"llama-server did not become healthy within {wait_s}s"
                return {
                    "ok": False,
                    "error": _last_start_error,
                    "log": wait_log,
                    "log_tail": tail,
                    "base_url": wait_base,
                    "vision_suspended": False,
                    "vision": wait_vision,
                    "detail": _last_health_detail,
                    "fail_kind": kind,
                    "autofit": wait_plan.to_public() if wait_plan else None,
                }
            _last_start_error = f"llama-server exited early (code {early_code})"
            return {
                "ok": False,
                "error": f"llama-server exited early (code {early_code}). See {wait_log}",
                "log": wait_log,
                "log_tail": tail,
                "vision_suspended": False,
                "vision": wait_vision,
                "fail_kind": kind,
                "autofit": wait_plan.to_public() if wait_plan else None,
            }

        retries += 1
        assert nxt is not None  # guaranteed by can_retry above
        wait_plan = nxt
        logger.warning(
            "RMB start %s — retry %s/%s with %s",
            kind,
            retries,
            max_retries,
            wait_plan.summary(),
        )
        remaining = 60.0 if timed_out else max(45.0, remaining * 0.7)
        with _lock:
            st_retry = merge_state(load_rmb_json(home_dir))
            apply_plan_to_state(st_retry, wait_plan)
            save_rmb_json(st_retry, home_dir)
            ctx = int(wait_plan.ctx_size)
            ngl = int(wait_plan.n_gpu_layers)
            wait_ctx = ctx
            wait_ngl = ngl
            _mark_starting(remaining + 30)
            enable_mtp = None
            if kind == "unknown_flag":
                enable_mtp = False
            cmd = _build_cmd(
                Path(wait_binary),
                Path(wait_model),
                host=wait_host,
                port=wait_port,
                ctx=ctx,
                ngl=ngl,
                threads=int(wait_plan.threads or 0),
                parallel=wait_use_parallel,
                flash_attn=bool(wait_plan.flash_attn),
                host_profile=wait_host_auto,
                enable_mtp=enable_mtp,
                **_engine_kwargs(st_retry),
            )
            if kind == "unknown_flag" and "--cache-reuse" in cmd:
                # Belt: strip even if probe was a false positive
                try:
                    i = cmd.index("--cache-reuse")
                    del cmd[i : i + 2]
                except ValueError:
                    pass
            try:
                _proc = _spawn(cmd)
            except Exception as e:
                _clear_starting()
                _set_vision_suspended(home_dir, False)
                if log_f is not subprocess.DEVNULL:
                    with contextlib.suppress(Exception):
                        log_f.close()
                return {
                    "ok": False,
                    "error": f"failed to respawn llama-server: {e}",
                    "vision_suspended": False,
                }
            wait_proc = _proc
            wait_pid = _proc.pid
            st_retry["pid"] = wait_pid
            st_retry["host_auto"] = wait_host_auto
            save_rmb_json(st_retry, home_dir)
            invalidate_cache()


def stop_rmb_server(
    home_dir: str | Path | None = None,
    *,
    resume_vision: bool = True,
    user_intent: bool = True,
) -> dict[str, Any]:
    """Stop RMB chat host; clear vision suspend; optionally restore SmolVLM.

    ``resume_vision=False`` for atexit/lifespan so we never restart Smol while
    the process is exiting.

    ``user_intent=True`` (default): mark user-stopped so background auto-wake
    will not restart until the user hits Start / selects a model.
    ``user_intent=False``: used when we are about to restart with a new GGUF —
    must not leave the host sticky on the old weights.
    """
    global _proc, _user_stopped
    with _lock:
        if user_intent:
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
            if ipid:
                # Always try kill for stored pid — failing to kill leaves the
                # previous GGUF sticky (Stop → pick new model still serves old).
                if _kill_pid(ipid):
                    killed = True
                else:
                    logger.warning("RMB: taskkill failed for pid %s", ipid)
        # Always free the RMB port — handles orphan llama-server after API restart
        try:
            port = int(state.get("port") or DEFAULT_CHAT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_CHAT_PORT
        if _kill_listeners_on_port(port):
            killed = True
        state["pid"] = None
        state["vision_suspended"] = False
        if user_intent:
            state["user_stopped"] = True
        save_rmb_json(state, home_dir)
        invalidate_cache()

    # Brief wait so the port is free before a restart spawn
    for _ in range(15):
        try:
            port = int(merge_state(load_rmb_json(home_dir)).get("port") or DEFAULT_CHAT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_CHAT_PORT
        if not _port_open(str(DEFAULT_HOST), port):
            break
        time.sleep(0.15)
        _kill_listeners_on_port(port)

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
    ensure_rmb_watchdog(home_dir)
    state = merge_state(load_rmb_json(home_dir))
    if is_running(home_dir, force=True, require_http=True):
        mark_used()
        with contextlib.suppress(Exception):
            adopt_existing_host(home_dir)
        return {"ok": True, "already_running": True, "base_url": state.get("base_url")}
    # Adopt orphan healthy host (API recycle) before spawning
    with contextlib.suppress(Exception):
        ad = adopt_existing_host(home_dir)
        if ad.get("ok") and is_running(home_dir, force=True, require_http=True):
            return {
                "ok": True,
                "already_running": True,
                "adopted": True,
                "base_url": state.get("base_url"),
            }
    if _refresh_user_stopped(home_dir) and not force:
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
    # rmb.json is the live control plane (Settings / stop / start). config.toml
    # only fills gaps — never re-force auto_start on after the user disabled it.
    disk = load_rmb_json(home)
    state = merge_state({**rmb_cfg, **disk})
    _refresh_user_stopped(home)
    # Soft auto-heal: only when auto_start is explicitly on (never default on)
    ensure_rmb_watchdog(home)
    # UI polls this every ~8s — honor the running cache. Heal only if auto_start.
    running = is_running(home, force=False, require_http=True)
    if (
        not running
        and not _user_stopped
        and state.get("enabled", True)
        and state.get("auto_start", False)
        and not is_starting()
    ):
        with contextlib.suppress(Exception):
            adopt_existing_host(home)
        running = is_running(home, force=True, require_http=True)
        if not running:
            with contextlib.suppress(Exception):
                wake_rmb_async(home)
    model_path = _resolve_model_path(state, home)
    binary = _find_llama_binary(state, home)
    ready = running
    # One loading probe per status call (is_loading + loading_for + stalled
    # used to stack 3 HTTP health checks on the 8s bar poll).
    loading_now = False if ready else bool(is_loading(home))
    starting = (not ready) and (is_starting() or loading_now)
    load_for = 0.0
    stalled = False
    if loading_now and _loading_since > 0:
        load_for = max(0.0, time.time() - _loading_since)
        stalled = load_for >= max(30.0, float(_LOADING_STALL_S))

    # Keep pid accurate for stop/UI even after API recycle (orphan adopt)
    pid_val: int | None = None
    try:
        raw_pid = state.get("pid")
        if raw_pid is not None:
            pid_val = int(raw_pid)
    except (TypeError, ValueError):
        pid_val = None
    if (running or loading_now) and (not pid_val or pid_val <= 0):
        with contextlib.suppress(Exception):
            found = _find_pid_on_port(int(state.get("port") or DEFAULT_CHAT_PORT))
            if found:
                pid_val = found
                state["pid"] = found
                with contextlib.suppress(Exception):
                    save_rmb_json(state, home)

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

    # Public name = live GGUF stem always (what status bar / chat use)
    chat_stem = model_path.stem if model_path is not None else str(state.get("model_id") or "")
    model_public = spec.to_public()
    if model_path is not None:
        model_public = {
            **model_public,
            "id": chat_stem,
            "filename": model_path.name,
            "name": chat_stem,
            "path": str(model_path),
        }
    restarts_recent = len(
        [
            t
            for t in _watchdog_restart_times
            if (time.time() - t) < _WATCHDOG_RESTART_WINDOW_S
        ]
    )
    return {
        "ok": True,
        "brand": "RMB",
        "brand_full": "Remedy Muscle Bridge",
        "engine_brand": "llama.cpp",
        "enabled": bool(state.get("enabled")),
        "auto_start": bool(state.get("auto_start", False)),
        "installed": installed,
        "running": running or starting,
        "ready": ready,
        "starting": starting,
        "loading": loading_now,
        "loading_for_s": round(load_for, 1) if load_for else 0,
        "loading_stalled": bool(stalled),
        "pid": pid_val,
        "managed_child": managed_process_alive(),
        "watchdog": bool(
            _watchdog_thread is not None and _watchdog_thread.is_alive()
        ),
        "watchdog_restarts_recent": restarts_recent,
        "health_detail": _last_health_detail or None,
        "last_error": _last_start_error,
        "user_stopped": bool(_user_stopped),
        "base_url": state.get("base_url"),
        "host": state.get("host"),
        "port": state.get("port"),
        "model_id": chat_stem or state.get("model_id"),
        "model": model_public,
        "chat_model": chat_stem or None,
        "llm_model": chat_stem or None,
        "model_path": str(model_path) if model_path else None,
        "model_present": model_path is not None,
        "runtime_binary": str(binary) if binary else None,
        "runtime_present": binary is not None,
        "ctx_size": int(state.get("ctx_size") or 8192),
        "n_gpu_layers": state.get("n_gpu_layers"),
        "profile": state.get("profile") or "autofit",
        "engine": {
            "threads": int(state.get("threads") or 0),
            "parallel": int(state.get("parallel") or 1),
            "flash_attn": bool(state.get("flash_attn", True)),
            "temperature": state.get("temperature"),
            "top_p": state.get("top_p"),
            "top_k": state.get("top_k"),
            "min_p": state.get("min_p"),
            "repeat_penalty": state.get("repeat_penalty"),
            "repeat_last_n": state.get("repeat_last_n"),
            "seed": state.get("seed"),
            "batch_size": state.get("batch_size"),
            "ubatch_size": state.get("ubatch_size"),
            "mmproj": state.get("mmproj") or "",
            "chat_template": state.get("chat_template") or "",
            "use_jinja": bool(state.get("use_jinja", True)),
            "rope_freq_scale": state.get("rope_freq_scale"),
            "rope_freq_base": state.get("rope_freq_base"),
            "mlock": bool(state.get("mlock", False)),
            "no_mmap": bool(state.get("no_mmap", False)),
            "cache_type": state.get("cache_type") or "",
            # KoboldCpp-class parity knobs
            "typical_p": state.get("typical_p"),
            "tfs_z": state.get("tfs_z"),
            "mirostat": state.get("mirostat"),
            "mirostat_tau": state.get("mirostat_tau"),
            "mirostat_eta": state.get("mirostat_eta"),
            "presence_penalty": state.get("presence_penalty"),
            "frequency_penalty": state.get("frequency_penalty"),
            "main_gpu": state.get("main_gpu"),
            "threads_batch": state.get("threads_batch"),
            "tensor_split": state.get("tensor_split") or "",
            "samplers": state.get("samplers") or "",
            "rope_scaling": state.get("rope_scaling") or "",
            "yarn_orig_ctx": state.get("yarn_orig_ctx"),
            "yarn_factor": state.get("yarn_factor"),
            "yarn_beta_fast": state.get("yarn_beta_fast"),
            "yarn_beta_slow": state.get("yarn_beta_slow"),
            "no_kv_offload": bool(state.get("no_kv_offload", False)),
            # DRY + XTC samplers
            "dry_multiplier": state.get("dry_multiplier"),
            "dry_base": state.get("dry_base"),
            "dry_allowed_length": state.get("dry_allowed_length"),
            "dry_penalty_last_n": state.get("dry_penalty_last_n"),
            "xtc_probability": state.get("xtc_probability"),
            "xtc_threshold": state.get("xtc_threshold"),
            "cache_reuse": state.get("cache_reuse"),
        },
        "nvidia": _nvidia_ok(),
        "has_gpu": _gpu_present(),
        "catalog": catalog_public(),
        "discovered_ggufs": discovered[:24],
        "not_ready_hint": (
            None
            if ready
            else (
                "Place any GGUF in ~/.remedy/rmb/models/ and click Start RMB"
                if not model_path
                else (
                    "Install llama-server (Local vision runtime once) then Start RMB"
                    if not binary
                    else (
                        "Loading model…"
                        if loading_now
                        else ("Starting…" if starting else "Start RMB to load the model")
                    )
                )
            )
        ),
        "local_agent_mode": local_agent or running,
        "skips_vision_stack": skip_vision or vision_suspended,
        "vision_suspended": vision_suspended,
        "host_auto": _status_host_auto(state, model_path),
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
        "autofit": _status_autofit(state, model_path, running=ready),
    }


def _status_host_auto(state: dict[str, Any], model_path: Path | None) -> dict[str, Any]:
    """Public auto-load card. Refresh when disk cache predates richer profiles.

    No GPU probe here — status is polled often; unfit is computed at start.
    """
    ha = state.get("host_auto") if isinstance(state.get("host_auto"), dict) else None
    if ha and ha.get("summary"):
        return ha
    # Filename-only — do not walk GGUF KV on the 8s status poll.
    return detect_gguf_host_profile(model_path, sniff_template=False)


def _status_autofit(
    state: dict[str, Any],
    model_path: Path | None,
    *,
    running: bool,
) -> dict[str, Any]:
    """Public autofit card for Settings / status."""
    enabled = should_autofit(state)
    locked = bool(state.get("autofit_locked"))
    last = state.get("last_autofit") if isinstance(state.get("last_autofit"), dict) else None
    out: dict[str, Any] = {
        "enabled": enabled,
        "locked": locked,
        "profile": state.get("profile") or "autofit",
        "last": last,
        "last_good": state.get("last_good_fit")
        if isinstance(state.get("last_good_fit"), dict)
        else None,
    }
    if last and running:
        out["summary"] = last.get("summary") or ""
        out["target"] = last.get("target")
        out["ctx_size"] = last.get("ctx_size")
        out["n_gpu_layers"] = last.get("n_gpu_layers")
        out["cache_type"] = last.get("cache_type")
        out["vram_total_mb"] = (last.get("hardware") or {}).get("vram_total_mb")
        return out
    if enabled and model_path is not None and not running and last:
        # Do not plan_autofit on GET — start / settings apply persist last_autofit.
        out["summary"] = last.get("summary") or ""
        out["target"] = last.get("target")
        out["ctx_size"] = last.get("ctx_size")
        out["n_gpu_layers"] = last.get("n_gpu_layers")
        out["cache_type"] = last.get("cache_type")
        out["vram_total_mb"] = (last.get("hardware") or {}).get("vram_total_mb")
    return out


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
        # inference-engine knobs are baked into the argv too — changing them on
        # disk alone does nothing until llama-server restarts with new flags.
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repeat_penalty",
        "repeat_last_n",
        "seed",
        "batch_size",
        "ubatch_size",
        "mmproj",
        "chat_template",
        "use_jinja",
        "rope_freq_scale",
        "rope_freq_base",
        "mlock",
        "no_mmap",
        "cache_type",
        # --- KoboldCpp-class parity knobs (all baked into argv) ---
        "typical_p",
        "tfs_z",
        "mirostat",
        "mirostat_tau",
        "mirostat_eta",
        "presence_penalty",
        "frequency_penalty",
        "main_gpu",
        "threads_batch",
        "tensor_split",
        "samplers",
        "rope_scaling",
        "yarn_orig_ctx",
        "yarn_factor",
        "yarn_beta_fast",
        "yarn_beta_slow",
        "no_kv_offload",
        # --- DRY + XTC samplers ---
        "dry_multiplier",
        "dry_base",
        "dry_allowed_length",
        "dry_penalty_last_n",
        "xtc_probability",
        "xtc_threshold",
        "cache_reuse",
    }
)


def _norm_rmb_val(key: str, val: Any) -> Any:
    """Normalize for process-diff comparison."""
    if key in (
        "ctx_size",
        "port",
        "threads",
        "parallel",
        "n_gpu_layers",
        "top_k",
        "repeat_last_n",
        "seed",
        "batch_size",
        "ubatch_size",
        "mirostat",
        "main_gpu",
        "threads_batch",
        "yarn_orig_ctx",
        "dry_allowed_length",
        "dry_penalty_last_n",
        "cache_reuse",
    ):
        try:
            return int(val) if val is not None and str(val).strip() != "" else val
        except (TypeError, ValueError):
            return val
    if key in ("temperature", "top_p", "min_p", "repeat_penalty", "rope_freq_scale", "rope_freq_base", "typical_p", "tfs_z", "mirostat_tau", "mirostat_eta", "presence_penalty", "frequency_penalty", "yarn_factor", "yarn_beta_fast", "yarn_beta_slow", "dry_multiplier", "dry_base", "xtc_probability", "xtc_threshold"):
        try:
            return float(val) if val is not None and str(val).strip() != "" else val
        except (TypeError, ValueError):
            return val
    if key in ("flash_attn", "use_jinja", "mlock", "no_mmap", "no_kv_offload"):
        return bool(val)
    if key in (
        "model_path",
        "runtime_binary",
        "host",
        "model_id",
        "runtime_id",
        "profile",
        "mmproj",
        "chat_template",
        "cache_type",
        "tensor_split",
        "samplers",
        "rope_scaling",
    ):
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


def apply_rmb_chat_model(
    model_hint: str | None,
    *,
    home_dir: str | Path | None = None,
    cfg: dict[str, Any] | None = None,
    live: bool = True,
    wait_s: float = 120.0,
) -> dict[str, Any]:
    """Switch RMB catalog/GGUF from a status-bar or settings model id/stem/path.

    Used when chat provider is already ``rmb`` and the user picks 7B vs 14B
    (or any catalog id, GGUF stem, or absolute path) — updates rmb.json and
    restarts the host so the loaded weights match the picker.
    """
    from remedy.runtime.rmb.catalog import catalog_id_from_hint

    hint = (model_hint or "").strip()
    if not hint:
        return {"ok": False, "error": "empty model"}

    # Absolute / relative path to a real GGUF
    hint_path = Path(hint)
    if hint_path.is_file() and hint_path.suffix.lower() == ".gguf":
        return apply_rmb_settings(
            {"model_path": str(hint_path.resolve()), "enabled": True},
            home_dir=home_dir,
            cfg=cfg,
            live=live,
            wait_s=wait_s,
        )

    # Match discovered files by filename or stem (any folder we scan)
    hint_l = hint.lower()
    if hint_l.endswith(".gguf"):
        hint_l = hint_l[:-5]
    for g in discover_ggufs(home_dir):
        raw = str(g.get("path") or "").strip()
        if not raw:
            continue
        p = Path(raw)
        name_l = p.name.lower()
        stem_l = p.stem.lower()
        if hint_l in (name_l, stem_l, name_l.replace(".gguf", "")):
            return apply_rmb_settings(
                {"model_path": str(p), "enabled": True},
                home_dir=home_dir,
                cfg=cfg,
                live=live,
                wait_s=wait_s,
            )

    # Catalog id / fuzzy stem → catalog model (re-resolves GGUF on disk)
    mid = catalog_id_from_hint(hint) or hint
    return apply_rmb_settings(
        {"model_id": mid, "enabled": True},
        home_dir=home_dir,
        cfg=cfg,
        live=live,
        wait_s=wait_s,
    )


def sync_rmb_chat_identity(
    state: dict[str, Any],
    *,
    home_dir: str | Path | None = None,
    force_provider: bool = False,
) -> dict[str, Any]:
    """Align config.toml + last_model so Provider / status bar match Loaded GGUF.

    Canonical chat model id for RMB is the **GGUF stem** (filename without
    .gguf). Catalog ids stay in rmb.json ``model_id`` for heuristics only.
    """
    out: dict[str, Any] = {"synced": False}
    path = str(state.get("model_path") or "").strip()
    if not path:
        return out
    try:
        stem = Path(path).stem
    except Exception:
        return out
    if not stem:
        return out
    base = str(state.get("base_url") or f"http://{DEFAULT_HOST}:{DEFAULT_CHAT_PORT}/v1")
    out["stem"] = stem
    out["model_path"] = path
    out["base_url"] = base
    try:
        from remedy.interfaces.api_support import (
            _find_config_path,
            _write_config,
            invalidate_config_cache,
            load_config,
        )

        invalidate_config_cache()
        disk = load_config()
        if not isinstance(disk, dict):
            return out
        last_by = dict(disk.get("last_model_by_provider") or {})
        want_last = dict(last_by)
        want_last["rmb"] = stem
        prov = str(disk.get("llm_provider") or "").strip().lower()
        steal = bool(force_provider or prov == "rmb")
        changed = want_last != last_by
        if steal:
            changed = changed or (
                prov != "rmb"
                or str(disk.get("llm_model") or "") != stem
                or str(disk.get("llm_base_url") or "") != base
            )
        if not changed:
            out["synced"] = True
            out["stem"] = stem
            return out
        disk["last_model_by_provider"] = want_last
        if steal:
            disk["llm_provider"] = "rmb"
            disk["llm_model"] = stem
            disk["llm_base_url"] = base
            disk["harness_mode"] = disk.get("harness_mode") or "auto"
            disk["harness_min_context_pct"] = 0.55
            disk["harness_max_context_pct"] = 0.78
            out["provider"] = "rmb"
            out["llm_model"] = stem
        cfg_path = _find_config_path()
        if cfg_path is not None:
            _write_config(cfg_path, disk)
            invalidate_config_cache()
            out["synced"] = True
            out["config_path"] = str(cfg_path)
    except Exception:
        logger.debug("RMB chat identity sync failed", exc_info=True)
    return out


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
    from remedy.runtime.rmb.catalog import catalog_id_from_hint

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
        # --- inference engine knobs (llama-server argv) ---
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repeat_penalty",
        "repeat_last_n",
        "seed",
        "batch_size",
        "ubatch_size",
        "mmproj",
        "chat_template",
        "use_jinja",
        "rope_freq_scale",
        "rope_freq_base",
        "mlock",
        "no_mmap",
        "cache_type",
        # --- KoboldCpp-class parity knobs (baked into argv) ---
        "typical_p",
        "tfs_z",
        "mirostat",
        "mirostat_tau",
        "mirostat_eta",
        "presence_penalty",
        "frequency_penalty",
        "main_gpu",
        "threads_batch",
        "tensor_split",
        "samplers",
        "rope_scaling",
        "yarn_orig_ctx",
        "yarn_factor",
        "yarn_beta_fast",
        "yarn_beta_slow",
        "no_kv_offload",
        # --- DRY + XTC samplers (KoboldCpp parity) ---
        "dry_multiplier",
        "dry_base",
        "dry_allowed_length",
        "dry_penalty_last_n",
        "xtc_probability",
        "xtc_threshold",
        "cache_reuse",
    ):
        if key not in patch:
            continue
        # Allow clearing path/string fields with ""
        if patch[key] is None and key not in (
            "model_path",
            "runtime_binary",
            "mmproj",
            "chat_template",
            "cache_type",
            "tensor_split",
            "samplers",
            "rope_scaling",
        ):
            continue
        if key == "ctx_size" and patch[key] is not None:
            try:
                # No hard cap — llama.cpp handles model-limited ctx at load.
                state[key] = max(2048, min(1048576, int(patch[key])))
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
        elif key == "mirostat" and patch[key] is not None:
            try:
                state[key] = max(0, min(2, int(patch[key])))
            except (TypeError, ValueError):
                continue
        elif key == "dry_penalty_last_n" and patch[key] is not None:
            try:
                state[key] = max(-1, min(65536, int(patch[key])))
            except (TypeError, ValueError):
                continue
        elif key in ("temperature", "top_p", "min_p", "repeat_penalty", "rope_freq_scale", "rope_freq_base", "typical_p", "tfs_z", "mirostat_tau", "mirostat_eta", "presence_penalty", "frequency_penalty", "yarn_factor", "yarn_beta_fast", "yarn_beta_slow", "dry_multiplier", "dry_base", "xtc_probability", "xtc_threshold") and patch[key] is not None:
            try:
                state[key] = float(patch[key])
            except (TypeError, ValueError):
                continue
        elif key in ("top_k", "repeat_last_n", "seed", "batch_size", "ubatch_size", "main_gpu", "threads_batch", "yarn_orig_ctx", "dry_allowed_length", "cache_reuse") and patch[key] is not None:
            try:
                state[key] = int(patch[key])
            except (TypeError, ValueError):
                continue
        elif key in ("use_jinja", "mlock", "no_mmap", "no_kv_offload") and patch[key] is not None:
            state[key] = bool(patch[key])
        else:
            state[key] = patch[key] if patch[key] is not None else ""

    # --- inference-engine knobs (baked into llama-server argv) ---
    _engine_float_ranges: dict[str, tuple[float, float]] = {
        "temperature": (0.0, 2.0),
        "top_p": (0.0, 1.0),
        "min_p": (0.0, 1.0),
        "repeat_penalty": (0.0, 2.0),
        "rope_freq_scale": (0.0, 10.0),
        "rope_freq_base": (0.0, 1_000_000.0),
        # KoboldCpp-class parity knobs
        "typical_p": (0.0, 1.0),
        "tfs_z": (0.0, 1.0),
        "mirostat_tau": (0.0, 100.0),
        "mirostat_eta": (0.0, 100.0),
        "presence_penalty": (-2.0, 10.0),
        "frequency_penalty": (-2.0, 10.0),
        "yarn_factor": (0.0, 100.0),
        "yarn_beta_fast": (0.0, 100.0),
        "yarn_beta_slow": (0.0, 100.0),
        # DRY + XTC samplers
        "dry_multiplier": (-2.0, 100.0),
        "dry_base": (0.0, 100.0),
        "xtc_probability": (0.0, 1.0),
        "xtc_threshold": (0.0, 1.0),
    }
    for key, (lo, hi) in _engine_float_ranges.items():
        if key not in patch or patch[key] is None:
            continue
        try:
            v = float(patch[key])
        except (TypeError, ValueError):
            continue
        if lo <= v <= hi:
            state[key] = v
    for key in ("top_k", "repeat_last_n", "seed", "batch_size", "ubatch_size", "main_gpu", "threads_batch", "yarn_orig_ctx", "dry_allowed_length", "cache_reuse"):
        if key not in patch or patch[key] is None:
            continue
        try:
            state[key] = int(patch[key])
        except (TypeError, ValueError):
            continue
    for key in ("use_jinja", "mlock", "no_mmap", "no_kv_offload"):
        if key not in patch or patch[key] is None:
            continue
        state[key] = bool(patch[key])
    for key in ("mmproj", "chat_template", "cache_type", "tensor_split", "samplers", "rope_scaling"):
        if key not in patch:
            continue
        state[key] = str(patch[key]).strip() if patch[key] else ""

    # Normalize model_id (status-bar stems → catalog ids)
    if "model_id" in patch and state.get("model_id"):
        mapped = catalog_id_from_hint(str(state.get("model_id")))
        if mapped:
            state["model_id"] = mapped

    # Catalog model change without an explicit new path → drop sticky GGUF so
    # 7B→14B (etc.) re-resolves instead of reloading the old file forever.
    old_mid = str(before.get("model_id") or "")
    new_mid = str(state.get("model_id") or "")
    if "model_id" in patch and "model_path" not in patch and old_mid != new_mid:
        state["model_path"] = ""

    # Explicit path: prefer GGUF stem as model_id for chat identity (status bar /
    # session llm). Catalog mapping is optional metadata only — never preferred
    # over the real filename (prevents Qwopus→Coder-7B snap).
    explicit_path = "model_path" in patch and bool(str(state.get("model_path") or "").strip())
    if explicit_path:
        with contextlib.suppress(Exception):
            stem = Path(str(state["model_path"])).stem
            if stem:
                state["model_id"] = stem

    if "profile" in patch and patch["profile"] in RMB_PROFILES:
        pid = str(patch["profile"])
        if pid == "autofit":
            state["autofit"] = True
            state["autofit_locked"] = False
            state["last_good_fit"] = None  # allow retrying the max fit
        else:
            state["autofit"] = False
            state["autofit_locked"] = True
            prof = RMB_PROFILES[pid]
            if "ctx_size" not in patch and int(prof.get("ctx_size") or 0) > 0:
                state["ctx_size"] = prof.get("ctx_size", state.get("ctx_size"))
            if "n_gpu_layers" not in patch:
                state["n_gpu_layers"] = prof.get("n_gpu_layers", state.get("n_gpu_layers"))

    # Editing ctx / GPU layers / KV cache locks autofit so we don't overwrite
    if any(k in patch for k in ("ctx_size", "n_gpu_layers", "cache_type")):
        if "profile" not in patch or str(patch.get("profile")) != "autofit":
            state["autofit_locked"] = True

    # Re-resolve path after model_id/path changes so disk + restart load the
    # correct GGUF (and config mirror gets the right llm_model stem).
    # CRITICAL: when the user picks an explicit GGUF path, trust it — do not
    # replace Downloads/foo.gguf with the first catalog 7B under models/.
    if "model_id" in patch or "model_path" in patch or not str(state.get("model_path") or "").strip():
        resolved = _resolve_model_path(
            state,
            home_dir,
            trust_sticky_path=explicit_path,
        )
        if resolved is not None:
            state["model_path"] = str(resolved)
        elif "model_id" in patch and "model_path" not in patch and old_mid != new_mid:
            # Leave empty — start will fail with a clear "GGUF not found" rather
            # than silently reusing a mismatched sticky path.
            state["model_path"] = ""

    # New GGUF (user changed path/id) → auto-load profile and forget the
    # previous file's last-good window. Do not treat auto-resolve of an
    # empty path during a ctx-only patch as a switch.
    user_switched = "model_path" in patch or "model_id" in patch
    new_path = str(state.get("model_path") or "").strip()
    old_path = str(before.get("model_path") or "").strip()
    path_changed = False
    if user_switched and new_path:
        try:
            path_changed = Path(new_path).name.lower() != (
                Path(old_path).name.lower() if old_path else ""
            )
        except OSError:
            path_changed = new_path.lower() != old_path.lower()
    elif user_switched and old_path and not new_path:
        path_changed = True
    if user_switched and new_path:
        hw_pub: dict[str, Any] | None = None
        with contextlib.suppress(Exception):
            hw_pub = probe_hardware().to_public()
        auto = detect_gguf_host_profile(new_path, hardware=hw_pub)
        preserve: set[str] = set()
        if "use_jinja" in patch:
            preserve.add("use_jinja")
        if "no_mmap" in patch:
            preserve.add("no_mmap")
        apply_host_profile_to_state(state, auto, preserve=preserve)
        if path_changed:
            state["last_good_fit"] = None
            if model_switch_should_refit(state):
                state["autofit"] = True
                state["autofit_locked"] = False

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

    # Steal chat provider only when the user asked. A model_id/path patch
    # must not yank OpenAI/xAI onto RMB.
    chat_sync = sync_rmb_chat_identity(
        state,
        home_dir=home_dir,
        force_provider=bool(patch.get("use_as_chat_provider")),
    )

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
        # Canonical chat id = GGUF stem (never get_model_spec fallback to 7B)
        chat_stem = ""
        if state.get("model_path"):
            with contextlib.suppress(Exception):
                chat_stem = Path(str(state["model_path"])).stem
        if not chat_stem:
            mid0 = str(state.get("model_id") or "")
            if mid0 in RMB_MODELS:
                chat_stem = get_model_spec(mid0).filename.replace(".gguf", "")
            else:
                chat_stem = mid0 or DEFAULT_RMB_MODEL_ID

        if state.get("enabled") and patch.get("use_as_chat_provider"):
            cfg["llm_provider"] = "rmb"
            cfg["llm_base_url"] = state.get("base_url")
            cfg["llm_model"] = chat_stem
            cfg["harness_mode"] = cfg.get("harness_mode") or "auto"
            cfg["harness_min_context_pct"] = 0.55
            cfg["harness_max_context_pct"] = 0.78
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
                last_by = dict(disk.get("last_model_by_provider") or {})
                if chat_stem:
                    last_by["rmb"] = chat_stem
                    disk["last_model_by_provider"] = last_by
                if state.get("enabled") and patch.get("use_as_chat_provider"):
                    disk["llm_provider"] = cfg["llm_provider"]
                    disk["llm_base_url"] = cfg["llm_base_url"]
                    disk["llm_model"] = chat_stem
                    disk["harness_min_context_pct"] = 0.55
                    disk["harness_max_context_pct"] = 0.78
                    disk_vision = disk.get("vision")
                    vis_cfg: dict[str, Any] = (
                        dict(disk_vision) if isinstance(disk_vision, dict) else {}
                    )
                    vis_cfg["force_decode"] = False
                    vis_cfg["auto_start"] = False
                    disk["vision"] = vis_cfg
                # When chat is already RMB, keep model/base_url + harness aligned
                # with live host so next turn doesn't use stale config.
                elif str(disk.get("llm_provider") or "").lower() == "rmb":
                    disk["llm_base_url"] = state.get("base_url")
                    if chat_stem:
                        disk["llm_model"] = chat_stem
                    disk["harness_min_context_pct"] = 0.55
                    disk["harness_max_context_pct"] = 0.78
                    cfg["llm_provider"] = "rmb"
                    cfg["llm_base_url"] = disk["llm_base_url"]
                    cfg["llm_model"] = disk["llm_model"]
                _write_config(path, disk)
                invalidate_config_cache()
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
            global _user_stopped
            # Disable → stop immediately
            if enabled_before and not enabled_now:
                stop_rmb_server(home_dir=home_dir, resume_vision=True, user_intent=True)
                live_meta["stopped"] = True
            # Model / ctx / GPU / port changed — always hard-stop then start so
            # we never keep the previous GGUF (even if "Stop" left an orphan
            # listener on :8787 or was_running was wrong).
            elif enabled_now and changed_process:
                logger.info(
                    "RMB live apply: force restart for %s (was_running=%s)",
                    ",".join(changed_process),
                    was_running,
                )
                stop_rmb_server(
                    home_dir=home_dir, resume_vision=False, user_intent=False
                )
                _user_stopped = False  # intentional restart, not user "stay off"
                live_meta["stopped"] = True
                start = start_rmb_server(
                    home_dir=home_dir,
                    wait_s=float(wait_s),
                    clear_user_stopped=True,
                )
                live_meta["started"] = bool(
                    start.get("ok") or start.get("starting")
                )
                live_meta["restarted"] = True
                if start.get("ok"):
                    live_meta["ctx_size_live"] = start.get("ctx_size")
                    sync_context_window_cache(
                        {**state, "ctx_size": start.get("ctx_size") or state.get("ctx_size")}
                    )
                    # Verify the live path matches what we intended
                    got = str(start.get("model_path") or state.get("model_path") or "")
                    want = str(state.get("model_path") or "")
                    if want and got and Path(want).name.lower() != Path(got).name.lower():
                        live_meta["live_error"] = (
                            f"Host loaded {Path(got).name} but wanted {Path(want).name}"
                        )
                elif start.get("already_running"):
                    # Should not happen after force stop — kill port and retry once
                    logger.warning("RMB: already_running after force stop; retry kill+start")
                    try:
                        port = int(state.get("port") or DEFAULT_CHAT_PORT)
                    except (TypeError, ValueError):
                        port = DEFAULT_CHAT_PORT
                    _kill_listeners_on_port(port)
                    time.sleep(0.3)
                    _user_stopped = False
                    start2 = start_rmb_server(
                        home_dir=home_dir,
                        wait_s=float(wait_s),
                        clear_user_stopped=True,
                    )
                    live_meta["started"] = bool(start2.get("ok") or start2.get("starting"))
                    if not start2.get("ok"):
                        live_meta["live_error"] = (
                            start2.get("error")
                            or "restart failed (old host still holding the port)"
                        )
                else:
                    live_meta["live_error"] = start.get("error") or "restart failed"
            # Enabled but not running: start so settings take effect now
            elif enabled_now and (not was_running) and (
                patch.get("enabled") is True
                or patch.get("use_as_chat_provider")
            ):
                _user_stopped = False
                start = start_rmb_server(
                    home_dir=home_dir,
                    wait_s=float(wait_s),
                    clear_user_stopped=True,
                )
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
    status["chat_sync"] = chat_sync
    # Canonical stem for UI (status bar + provider form)
    if chat_sync.get("stem"):
        status["chat_model"] = chat_sync["stem"]
        status["llm_model"] = chat_sync["stem"]
    elif status.get("model_path"):
        with contextlib.suppress(Exception):
            status["chat_model"] = Path(str(status["model_path"])).stem
            status["llm_model"] = status["chat_model"]
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
