"""Shared Desktop-host poller control for live test scripts.

Starts ``computer_host_poller.py`` as a subprocess so computer-use tools
see ``host_connected=True`` without a full Tauri Desktop.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_POLLER = _REPO / "scripts" / "computer_host_poller.py"
_proc: subprocess.Popen | None = None


def _api_base() -> str:
    return os.environ.get("REMEDY_API", "http://127.0.0.1:7400").rstrip("/")


def _token() -> str:
    home = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
    p = home / "auth" / "local_api_token"
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return ""


def host_connected(timeout: float = 3.0) -> bool:
    tok = _token()
    headers = {"Accept": "application/json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(
        f"{_api_base()}/api/computer/host/status", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8") or "{}")
            return bool(data.get("host_connected"))
    except Exception:
        return False


def start_host_poller(*, wait_connected: float = 8.0) -> bool:
    """Start poller if not already connected. Returns True when host is connected."""
    global _proc
    if host_connected():
        return True
    if not _POLLER.is_file():
        print(f"  [poller] missing {_POLLER}", flush=True)
        return False
    env = os.environ.copy()
    env.setdefault("REMEDY_API", _api_base())
    env.setdefault(
        "REMEDY_HOME",
        str(Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()),
    )
    # Avoid re-spawning if we already own a live process
    if _proc is not None and _proc.poll() is None:
        deadline = time.time() + wait_connected
        while time.time() < deadline:
            if host_connected():
                return True
            time.sleep(0.25)
        return host_connected()

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _proc = subprocess.Popen(
        [sys.executable, str(_POLLER)],
        cwd=str(_REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    atexit.register(stop_host_poller)
    deadline = time.time() + wait_connected
    while time.time() < deadline:
        if host_connected():
            print("  [poller] host_connected=True", flush=True)
            return True
        if _proc.poll() is not None:
            print(f"  [poller] exited early code={_proc.returncode}", flush=True)
            return False
        time.sleep(0.25)
    ok = host_connected()
    print(f"  [poller] ready={ok}", flush=True)
    return ok


def stop_host_poller() -> None:
    global _proc
    if _proc is None:
        return
    try:
        if _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _proc.kill()
    except Exception:
        pass
    _proc = None
