"""Manage the local llama-server process for the visual decoder."""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import subprocess
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


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _health(base_url: str, timeout: float = 2.0) -> bool:
    url = base_url.rstrip("/") + "/models"
    try:
        req = Request(url, headers={"User-Agent": "RemedyAI-vision/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        # Some builds only expose /v1/models
        try:
            req = Request(
                base_url.rstrip("/") + "/v1/models"
                if not base_url.rstrip("/").endswith("/v1")
                else base_url.rstrip("/") + "/models",
                headers={"User-Agent": "RemedyAI-vision/1.0"},
            )
            with urlopen(req, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except Exception:
            return False


def is_running(home_dir: str | Path | None = None) -> bool:
    global _proc
    state = load_vision_json(home_dir)
    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    if _proc is not None and _proc.poll() is None:
        return _health(base) or _port_open(host, port)
    return _health(base)


def mark_used() -> None:
    global _last_used
    _last_used = time.time()


def start_server(
    *,
    home_dir: str | Path | None = None,
    n_gpu_layers: int = -1,
    wait_s: float = 60.0,
) -> dict[str, Any]:
    """Start llama-server if not already healthy."""
    global _proc
    state = load_vision_json(home_dir)
    if not state:
        return {"ok": False, "error": "Vision decoder not installed (no vision.json)"}

    host = str(state.get("host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_PORT)
    base = str(state.get("base_url") or f"http://{host}:{port}/v1")
    model_path = state.get("model_path")
    mmproj_path = state.get("mmproj_path")
    if not model_path or not Path(str(model_path)).is_file():
        return {"ok": False, "error": f"Model file missing: {model_path}"}
    if not mmproj_path or not Path(str(mmproj_path)).is_file():
        return {"ok": False, "error": f"mmproj file missing: {mmproj_path}"}

    if is_running(home_dir):
        mark_used()
        return {"ok": True, "already_running": True, "base_url": base, "pid": _pid()}

    binary = runtime_binary_path(home_dir)
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

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if _proc.poll() is not None:
            return {
                "ok": False,
                "error": f"llama-server exited early (code {_proc.returncode})",
            }
        if _health(base) or _port_open(host, port):
            # Prefer HTTP health; port open is weak but better than hang
            if _health(base, timeout=1.0) or time.time() > deadline - 2:
                mark_used()
                state["pid"] = _proc.pid
                save_vision_json(state, home_dir)
                return {
                    "ok": True,
                    "already_running": False,
                    "base_url": base,
                    "pid": _proc.pid,
                }
        time.sleep(0.4)

    return {"ok": False, "error": f"llama-server did not become healthy within {wait_s}s"}


def stop_server(home_dir: str | Path | None = None) -> dict[str, Any]:
    global _proc
    killed = False
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _proc.kill()
        killed = True
    _proc = None
    state = load_vision_json(home_dir)
    if state.get("pid"):
        state.pop("pid", None)
        with contextlib.suppress(Exception):
            save_vision_json(state, home_dir)
    return {"ok": True, "stopped": killed}


def _pid() -> int | None:
    if _proc is not None and _proc.poll() is None:
        return _proc.pid
    return None


def maybe_idle_stop(idle_stop_s: int, home_dir: str | Path | None = None) -> None:
    global _last_used
    if idle_stop_s <= 0 or _last_used <= 0:
        return
    if time.time() - _last_used < idle_stop_s:
        return
    if is_running(home_dir):
        logger.info("Vision decoder idle timeout — stopping llama-server")
        stop_server(home_dir=home_dir)
