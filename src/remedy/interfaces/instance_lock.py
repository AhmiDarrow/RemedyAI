"""Single-process ownership so only one Remedy Desktop / serve stack runs.

Desktop holds a Windows named mutex (see Tauri). The Python API (``remedy serve``)
holds ``~/.remedy/locks/remedy_serve.lock`` so a second CLI serve cannot bind
7400 under the first. Sidecar children set ``REMEDY_DESKTOP_SIDECAR=1`` and still
take the serve lock (only one API).
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STALE_LOCK_SECONDS = 120.0
_LOCK_NAME = "remedy_serve.lock"
_held_fh = None
_held_path: Path | None = None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return int(code.value) == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_path(home: Path | str | None = None) -> Path:
    base = Path(home).expanduser() if home else Path.home() / ".remedy"
    return base / "locks" / _LOCK_NAME


def try_acquire_serve_lock(home: Path | str | None = None) -> tuple[bool, str]:
    """Acquire exclusive serve lock. Returns (ok, message)."""
    global _held_fh, _held_path
    path = lock_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Fail closed — dual serve races messengers/retention writers.
        # Tests can set REMEDY_SERVE_LOCK_FAIL_OPEN=1 for ephemeral FS.
        import os as _os

        if str(_os.environ.get("REMEDY_SERVE_LOCK_FAIL_OPEN") or "").strip() in (
            "1",
            "true",
            "yes",
        ):
            logger.warning("serve lock mkdir failed: %s — fail-open (env)", e)
            return True, "lock dir unavailable; continuing (fail-open)"
        logger.error("serve lock mkdir failed: %s — refusing dual serve", e)
        return False, f"lock dir unavailable ({e}); cannot start serve safely"

    # Stale reclaim
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8").strip().split()
            old_pid = int(raw[0]) if raw else 0
            old_ts = float(raw[1]) if len(raw) > 1 else 0.0
        except (OSError, ValueError):
            old_pid, old_ts = 0, 0.0
        now = time.time()
        stale = (
            old_pid <= 0
            or not _pid_alive(old_pid)
            or (old_ts > 0 and (now - old_ts) > STALE_LOCK_SECONDS)
        )
        if stale:
            with contextlib.suppress(OSError):
                path.unlink()
        elif old_pid == os.getpid():
            return True, "already held by this process"
        else:
            return (
                False,
                f"Remedy is already running (API lock held by pid={old_pid}). "
                "Quit the other Remedy Desktop / serve first, or close the process "
                f"using {path}.",
            )

    try:
        # Exclusive create
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if sys.platform == "win32":
            flags |= os.O_BINARY
        fd = os.open(str(path), flags, 0o600)
        fh = os.fdopen(fd, "r+", encoding="utf-8")
        fh.write(f"{os.getpid()} {time.time():.3f}\n")
        fh.flush()
        _held_fh = fh
        _held_path = path
        atexit.register(release_serve_lock)
        return True, "acquired"
    except FileExistsError:
        return (
            False,
            "Remedy is already running (could not create serve lock). "
            "Quit the other instance first.",
        )
    except OSError as e:
        import os as _os

        if str(_os.environ.get("REMEDY_SERVE_LOCK_FAIL_OPEN") or "").strip() in (
            "1",
            "true",
            "yes",
        ):
            logger.warning("serve lock acquire failed: %s — fail-open (env)", e)
            return True, f"lock open failed; continuing ({e})"
        logger.error("serve lock acquire failed: %s — refusing dual serve", e)
        return False, f"lock open failed ({e}); cannot start serve safely"


def heartbeat_serve_lock() -> None:
    """Refresh lock timestamp while serve is alive."""
    global _held_fh, _held_path
    if _held_fh is None or _held_path is None:
        return
    try:
        _held_fh.seek(0)
        _held_fh.truncate()
        _held_fh.write(f"{os.getpid()} {time.time():.3f}\n")
        _held_fh.flush()
    except OSError:
        pass


def release_serve_lock() -> None:
    global _held_fh, _held_path
    fh, path = _held_fh, _held_path
    _held_fh = None
    _held_path = None
    if fh is not None:
        with contextlib.suppress(Exception):
            fh.close()
    if path is not None and path.is_file():
        try:
            raw = path.read_text(encoding="utf-8").strip().split()
            if raw and int(raw[0]) == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
