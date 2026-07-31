"""Keep ``remedy serve`` alive on :7400 for Desktop.

Desktop Tauri force-kills :7400 when restarting its sidecar; if that spawn
fails, the UI shows "Failed to fetch". This watchdog restarts serve within
a few seconds whenever the port is down.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
LOG = HOME / "logs" / "serve_watchdog.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def api_up() -> bool:
    for url in (
        "http://127.0.0.1:7400/api/ping",
        "http://127.0.0.1:7400/api/status",
    ):
        try:
            with urllib.request.urlopen(url, timeout=2.0) as r:
                if r.status in (200, 401, 403):
                    return True
        except Exception:
            continue
    return False


def clear_stale_lock() -> None:
    lock = HOME / "locks" / "remedy_serve.lock"
    if lock.is_file() and not api_up():
        try:
            lock.unlink()
            log(f"cleared stale lock {lock}")
        except OSError as e:
            log(f"lock clear failed: {e}")


def spawn_serve() -> subprocess.Popen | None:
    clear_stale_lock()
    env = os.environ.copy()
    env["REMEDY_HOME"] = str(HOME)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["REMEDY_DESKTOP_SIDECAR"] = "1"
    out = open(HOME / "logs" / "serve_stdout.log", "a", encoding="utf-8")
    err = open(HOME / "logs" / "serve_stderr.log", "a", encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "scripts" / "_start_serve.py")]
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=flags,
            close_fds=True,
        )
        log(f"spawned serve pid={p.pid}")
        return p
    except Exception as e:
        log(f"spawn failed: {e}")
        return None


def main() -> int:
    log("watchdog starting")
    child: subprocess.Popen | None = None
    while True:
        try:
            if api_up():
                time.sleep(4.0)
                continue
            log("API down — restarting serve")
            if child is not None and child.poll() is None:
                try:
                    child.terminate()
                    child.wait(timeout=3)
                except Exception:
                    with contextlib.suppress(Exception):
                        child.kill()
            child = spawn_serve()
            # Wait for health
            for i in range(40):
                time.sleep(0.5)
                if api_up():
                    log(f"API up after {i * 0.5:.1f}s")
                    break
            else:
                log("API still down after spawn wait")
            time.sleep(3.0)
        except KeyboardInterrupt:
            log("watchdog stop")
            return 0
        except Exception as e:
            log(f"watchdog tick error: {e}")
            time.sleep(5.0)


if __name__ == "__main__":
    raise SystemExit(main())
