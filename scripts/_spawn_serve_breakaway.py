"""Spawn ``remedy serve`` outside the current Job Object so it survives tool shells.

On Windows, agent shells often use a Job Object that kills children when the
command ends — which is why Desktop sees "Failed to fetch" after we "start"
serve from a one-shot command.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
LOG = HOME / "logs" / "serve_spawn.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    print(msg, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def api_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:7400/api/ping", timeout=2) as r:
            return r.status == 200
    except Exception:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:7400/api/status",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status in (200, 401, 403)
        except Exception:
            return False


def main() -> int:
    os.environ["REMEDY_HOME"] = str(HOME)
    os.environ["PYTHONPATH"] = str(ROOT / "src")
    # Clear stale lock if nothing is listening
    if not api_up():
        lock = HOME / "locks" / "remedy_serve.lock"
        if lock.is_file():
            try:
                lock.unlink()
                log(f"removed stale lock {lock}")
            except OSError as e:
                log(f"lock remove failed: {e}")

    if api_up():
        log("API already up on :7400")
        return 0

    out = open(HOME / "logs" / "serve_stdout.log", "a", encoding="utf-8")
    err = open(HOME / "logs" / "serve_stderr.log", "a", encoding="utf-8")
    flags = (
        CREATE_BREAKAWAY_FROM_JOB
        | CREATE_NEW_PROCESS_GROUP
        | CREATE_NO_WINDOW
        | DETACHED_PROCESS
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "_start_serve.py"),
    ]
    log(f"spawning breakaway: {cmd}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            close_fds=True,
            creationflags=flags,
        )
    except TypeError:
        # Non-Windows
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
    log(f"spawned pid={proc.pid}")
    for i in range(30):
        time.sleep(0.5)
        if api_up():
            log(f"API healthy after {i * 0.5:.1f}s")
            return 0
        if proc.poll() is not None:
            log(f"serve exited early code={proc.returncode}")
            return 1
    log("timeout waiting for API")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
