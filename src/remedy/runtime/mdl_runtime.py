"""Multi-tier llama-server management for MDL density-gated inference.

Manages 3 llama-server instances on different ports, each loading a tier
subset of the shared model. The LIGHT tier is always resident (4 layers).
MEDIUM and FULL tiers start on demand and idle-stop after use.
"""

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

from remedy.runtime.mdl import MDL_TIERS, get_tier_base_url

logger = logging.getLogger(__name__)

# Per-tier state
_tier_procs: dict[str, subprocess.Popen[Any] | None] = {
    "light": None,
    "medium": None,
    "full": None,
}
_tier_last_used: dict[str, float] = {
    "light": 0.0,
    "medium": 0.0,
    "full": 0.0,
}
_tier_lock = threading.Lock()
_tier_health_cache: dict[str, dict[str, Any]] = {}
_tier_running_watcher = False

# Defaults
DEFAULT_HOST = "127.0.0.1"
IDLE_STOP_S = 600  # default idle stop for non-resident tiers


def _port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _health(base_url: str, timeout: float = 0.5) -> bool:
    from urllib.request import Request

    from remedy.core.security import is_loopback_service_url, urlopen_no_redirect

    base = (base_url or "").rstrip("/")
    if not base or not is_loopback_service_url(base):
        return False
    url = base + "/models"
    try:
        req = Request(url, headers={"User-Agent": "RemedyAI-mdl/1.0"})
        with urlopen_no_redirect(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        try:
            alt = base + "/v1/models" if not base.endswith("/v1") else base + "/models"
            req = Request(alt, headers={"User-Agent": "RemedyAI-mdl/1.0"})
            with urlopen_no_redirect(req, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except Exception:
            return False


def is_tier_running(tier_name: str) -> bool:
    """Check if a tier's llama-server is healthy."""
    proc = _tier_procs.get(tier_name)
    tier = MDL_TIERS.get(tier_name)
    if tier is None:
        return False
    child_alive = proc is not None and proc.poll() is None
    port_up = _port_open(DEFAULT_HOST, tier.port)
    if child_alive and port_up:
        return True
    if port_up:
        base = get_tier_base_url(tier_name)
        return _health(base)
    return False


def mark_tier_used(tier_name: str) -> None:
    if tier_name in _tier_last_used:
        _tier_last_used[tier_name] = time.time()


def tier_idle_stop(tier_name: str, idle_s: float = IDLE_STOP_S) -> bool:
    """Stop a tier if it has been idle for idle_s and is not resident."""
    tier = MDL_TIERS.get(tier_name)
    if tier is None:
        return False
    if tier.resident:
        return False  # never stop resident tier
    age = time.time() - _tier_last_used.get(tier_name, 0)
    if age < idle_s:
        return False
    return stop_tier(tier_name)


def stop_tier(tier_name: str) -> dict[str, Any]:
    """Stop a single tier's llama-server."""
    tier = MDL_TIERS.get(tier_name)
    if tier is None:
        return {"ok": False, "error": f"Unknown tier: {tier_name}"}

    proc = _tier_procs.get(tier_name)
    killed = False

    if proc is not None and proc.poll() is None:
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
        _tier_procs[tier_name] = None

    if os.name == "nt" and proc is not None and proc.pid:
        try:
            args = ["taskkill", "/F", "/PID", str(proc.pid), "/T"]
            subprocess.run(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            killed = True
        except (OSError, subprocess.TimeoutExpired):
            pass

    return {"ok": True, "stopped": killed, "tier": tier_name}


def start_tier(
    tier_name: str,
    *,
    model_path: str,
    mmproj_path: str | None = None,
    runtime_binary: str,
    n_gpu_layers: int | None = None,
    wait_s: float = 30.0,
) -> dict[str, Any]:
    """Start a single tier's llama-server instance."""
    tier = MDL_TIERS.get(tier_name)
    if tier is None:
        return {"ok": False, "error": f"Unknown tier: {tier_name}"}

    if is_tier_running(tier_name):
        mark_tier_used(tier_name)
        return {
            "ok": True,
            "already_running": True,
            "base_url": get_tier_base_url(tier_name),
            "tier": tier_name,
        }

    host = DEFAULT_HOST
    port = tier.port

    # A child we already spawned may still be loading its weights. Spawning a
    # second one would double the VRAM and race for the port; killing the first
    # because it is slow would mean a tier that needs longer than wait_s can
    # never come up. So wait on the one we have, and only ever spawn when the
    # slot is empty or holds a corpse.
    existing = _tier_procs.get(tier_name)
    if existing is not None:
        if existing.poll() is None:
            return _await_ready(tier_name, existing, host, port, wait_s, already_running=True)
        logger.info(
            "MDL tier %s exited (code %s) before becoming ready; respawning",
            tier_name,
            existing.returncode,
        )
        _tier_procs[tier_name] = None

    binary = Path(runtime_binary)
    if not binary.is_file():
        return {"ok": False, "error": f"llama-server binary not found: {runtime_binary}"}

    mpath = Path(model_path)
    if not mpath.is_file():
        return {"ok": False, "error": f"Model file not found: {model_path}"}

    cmd: list[str] = [
        str(binary),
        "-m", str(model_path),
        "--host", host,
        "--port", str(port),
        "--ctx-size", "4096",
    ]

    cmd.extend(["-ngl", str(n_gpu_layers) if n_gpu_layers is not None else str(tier.n_layers)])

    if mmproj_path and Path(mmproj_path).is_file():
        cmd.extend(["--mmproj", str(mmproj_path)])

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    logger.info("Starting MDL tier %s: %s", tier_name, " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(binary.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        _tier_procs[tier_name] = proc
    except OSError as e:
        return {"ok": False, "error": f"Failed to start tier {tier_name}: {e}"}

    return _await_ready(tier_name, proc, host, port, wait_s, already_running=False)


def _await_ready(
    tier_name: str,
    proc: subprocess.Popen[Any],
    host: str,
    port: int,
    wait_s: float,
    *,
    already_running: bool,
) -> dict[str, Any]:
    """Wait up to ``wait_s`` for a registered child to answer its health probe.

    A child that *exits* is a failure and its slot is cleared. A child that is
    merely still loading when the clock runs out stays registered and is
    reported as ``starting`` rather than failed, so the next call picks it up
    where this one left off instead of spawning a twin beside it.
    """
    deadline = time.time() + wait_s
    while True:
        current = _tier_procs.get(tier_name)
        if current is None:
            return {"ok": False, "error": f"Tier {tier_name} proc cleared during start"}
        if current is not proc:
            # Someone else replaced the slot; that start owns the wait now.
            return {"ok": False, "error": f"Tier {tier_name} restarted concurrently"}
        if proc.poll() is not None:
            _tier_procs[tier_name] = None
            return {
                "ok": False,
                "error": f"Tier {tier_name} exited early (code {proc.returncode})",
            }
        if _port_open(host, port) and _health(get_tier_base_url(tier_name), timeout=0.6):
            mark_tier_used(tier_name)
            return {
                "ok": True,
                "already_running": already_running,
                "base_url": get_tier_base_url(tier_name),
                "tier": tier_name,
                "pid": proc.pid,
            }
        if time.time() >= deadline:
            break
        time.sleep(0.4)

    return {
        "ok": False,
        "starting": True,
        "pid": proc.pid,
        "tier": tier_name,
        "error": f"Tier {tier_name} still loading after {wait_s}s; not ready yet",
    }

def ensure_tier(
    tier_name: str,
    *,
    model_path: str,
    mmproj_path: str | None = None,
    runtime_binary: str,
    n_gpu_layers: int | None = None,
) -> dict[str, Any]:
    """Ensure a tier is running, starting it if necessary."""
    if is_tier_running(tier_name):
        mark_tier_used(tier_name)
        return {
            "ok": True,
            "base_url": get_tier_base_url(tier_name),
            "tier": tier_name,
        }
    return start_tier(
        tier_name,
        model_path=model_path,
        mmproj_path=mmproj_path,
        runtime_binary=runtime_binary,
        n_gpu_layers=n_gpu_layers,
    )


def stop_all_tiers() -> dict[str, Any]:
    """Stop all running tiers."""
    results = {}
    for name in MDL_TIERS:
        results[name] = stop_tier(name)
    return {"ok": True, "tiers": results}


def ensure_idle_watcher(resident_first: bool = True) -> None:
    """Start background thread to idle-stop non-resident tiers."""
    global _tier_running_watcher
    if _tier_running_watcher:
        return
    _tier_running_watcher = True

    def _loop() -> None:
        while True:
            try:
                time.sleep(30.0)
                for name, tier in MDL_TIERS.items():
                    if tier.resident:
                        continue
                    tier_idle_stop(name)
            except Exception:
                logger.debug("MDL idle watcher tick failed", exc_info=True)

    t = threading.Thread(target=_loop, name="remedy-mdl-idle-stop", daemon=True)
    t.start()

    if resident_first:
        # Start LIGHT tier if config is available
        with contextlib.suppress(Exception):
            from remedy.vision.config import load_vision_json
            from remedy.vision.install import runtime_binary_path

            vstate = load_vision_json()
            model_path = vstate.get("model_path")
            mmproj_path = vstate.get("mmproj_path")
            rb = vstate.get("runtime_binary")

            if not rb or not Path(rb).is_file():
                rb_path = runtime_binary_path()
                rb = str(rb_path) if rb_path else None

            if model_path and rb:
                start_tier(
                    "light",
                    model_path=str(model_path),
                    mmproj_path=str(mmproj_path) if mmproj_path else None,
                    runtime_binary=str(rb),
                )


def tier_status() -> dict[str, Any]:
    """Return status of all tiers."""
    result: dict[str, Any] = {"tiers": {}}
    for name in MDL_TIERS:
        result["tiers"][name] = {
            "running": is_tier_running(name),
            "base_url": get_tier_base_url(name) if is_tier_running(name) else None,
            "last_used": _tier_last_used.get(name, 0),
        }
    result["ok"] = True
    return result
