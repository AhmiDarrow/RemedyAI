"""Exclusive long-poll ownership so only one Remedy process polls a messenger bot.

Telegram returns HTTP 409 when two getUpdates pollers share a bot token — realtime
dies for the loser and feels like "sync stuck". Hold a PID + heartbeat lock file
under ``~/.remedy/locks/``.

On serve restart the new process must be able to take over a leftover file from a
*dead* previous process (Windows STILL_ACTIVE / PID reuse used to look "live"
and lock the new poller out). The OS exclusive lock is the source of truth:
a leftover file whose flock/msvcrt lock is free is reclaimed; a true live
holder still wins.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from remedy.home import default_home

logger = logging.getLogger(__name__)

# If the lock owner stops heartbeating, another process may take over.
STALE_LOCK_SECONDS = 90.0


def _pid_alive(pid: int) -> bool:
    """True only if the OS process is still running (not merely OpenProcess-able).

    On Windows, ``OpenProcess`` can succeed for *exited* PIDs that still have a
    kernel object (common after crash/restart). Use ``GetExitCodeProcess`` and
    require ``STILL_ACTIVE`` (259).
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            access = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
            handle = kernel32.OpenProcess(access, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                if not ok:
                    return False
                # 259 = STILL_ACTIVE
                return int(code.value) == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_lock_payload(raw: str) -> tuple[int, float] | None:
    parts = (raw or "").strip().split()
    if not parts:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    ts = 0.0
    if len(parts) >= 2:
        with contextlib.suppress(ValueError):
            ts = float(parts[1])
    return pid, ts


# In-process holders: flock is re-entrant on Unix, so a second MessengerPollLock
# in the same process must not start a dual getUpdates poller.
_PROCESS_HOLDERS: dict[str, MessengerPollLock] = {}


class MessengerPollLock:
    """Non-blocking exclusive lock for one messenger channel poller.

    Use as a context manager for safe cleanup::

        with MessengerPollLock(home, "telegram") as lock:
            if lock.held:
                ... poll ...
    """

    def __init__(self, home: Path | str | None, channel: str = "telegram") -> None:
        base = Path(home).expanduser() if home else default_home()
        self.path = base / "locks" / f"{channel}_getupdates.lock"
        self.channel = channel
        self._fh: TextIO | None = None  # keep open so Windows exclusive share holds
        self.held = False
        self.reclaimed = False  # True if we took over a leftover file this acquire

    def __enter__(self) -> MessengerPollLock:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.release()

    def _key(self) -> str:
        try:
            return str(self.path.resolve())
        except OSError:
            return str(self.path)

    def try_acquire(self) -> bool:
        """Return True if this process owns the poller. False → do not start poll loop.

        Never refuse solely because a PID in the file looks alive: Windows can
        report STILL_ACTIVE for an exited process, and PIDs recycle. Try the OS
        exclusive lock. If it is free, this serve takes over (restart reclaim).
        If it is busy, a true live poller still holds it — we stay out.
        """
        if self.held:
            return True
        key = self._key()
        other = _PROCESS_HOLDERS.get(key)
        if other is not None and other is not self and getattr(other, "held", False):
            fh = getattr(other, "_fh", None)
            still = fh is not None and not getattr(fh, "closed", True)
            if still:
                logger.warning(
                    "%s poll lock already held in-process — not starting long-poll (avoid dual poll)",
                    self.channel,
                )
                return False
            # Handle gone: leftover holder from a failed stop. Reclaim.
            logger.info(
                "%s poll lock reclaim (stale in-process holder)",
                self.channel,
            )
            with contextlib.suppress(Exception):
                other.held = False
                _PROCESS_HOLDERS.pop(key, None)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("%s poll lock mkdir failed: %s", self.channel, e)
            return False

        foreign_pid: int | None = None
        foreign_alive = False

        # Leftover file: previous owner dead, heartbeat expired, or PID reuse.
        # Unlink only when we *know* the PID is dead (Windows cannot delete a
        # file another process still has locked). If the PID looks live, leave
        # the file and let the OS exclusive lock decide.
        if self.path.is_file():
            try:
                raw = self.path.read_text(encoding="utf-8")
                parsed = _parse_lock_payload(raw)
                if parsed is not None:
                    old_pid, old_ts = parsed
                    now = time.time()
                    stale_hb = old_ts > 0 and (now - old_ts) > STALE_LOCK_SECONDS
                    if old_pid != os.getpid():
                        foreign_pid = old_pid
                        foreign_alive = _pid_alive(old_pid)
                        if not foreign_alive:
                            why = "stale heartbeat" if stale_hb else "dead pid"
                            logger.info(
                                "%s poll lock reclaim (%s pid=%s age=%.0fs)",
                                self.channel,
                                why,
                                old_pid,
                                (now - old_ts) if old_ts else -1,
                            )
                            with contextlib.suppress(OSError):
                                self.path.unlink(missing_ok=True)
                        else:
                            logger.info(
                                "%s poll lock file names live pid=%s age=%.0fs — "
                                "trying OS lock (reclaim if free; refuse if busy)",
                                self.channel,
                                old_pid,
                                (now - old_ts) if old_ts else -1,
                            )
            except (OSError, ValueError, IndexError):
                # Windows: msvcrt lock can make read_text PermissionError while
                # another process still holds the byte lock. Fall through and
                # try the OS exclusive lock — that is the real exclusion.
                pass

        try:
            # Exclusive create/truncate and hold the handle open.
            self._fh = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    self._fh.close()
                    self._fh = None
                    if foreign_alive and foreign_pid is not None:
                        logger.warning(
                            "%s poll lock held by live pid=%s — this process will not "
                            "long-poll (avoid HTTP 409). Quit the other Remedy instance "
                            "or wait for it to exit.",
                            self.channel,
                            foreign_pid,
                        )
                    else:
                        logger.warning(
                            "%s poll lock busy (msvcrt) — not starting long-poll",
                            self.channel,
                        )
                    return False
            else:
                import fcntl

                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    self._fh.close()
                    self._fh = None
                    if foreign_alive and foreign_pid is not None:
                        logger.warning(
                            "%s poll lock held by live pid=%s — this process will not "
                            "long-poll (avoid HTTP 409). Quit the other Remedy instance "
                            "or wait for it to exit.",
                            self.channel,
                            foreign_pid,
                        )
                    else:
                        logger.warning(
                            "%s poll lock busy (fcntl) — not starting long-poll",
                            self.channel,
                        )
                    return False

            self._write_payload()
            self.held = True
            self.reclaimed = bool(foreign_pid is not None and foreign_pid != os.getpid())
            _PROCESS_HOLDERS[self._key()] = self
            if self.reclaimed:
                logger.info(
                    "%s poll lock acquired (pid=%s took over pid=%s path=%s)",
                    self.channel,
                    os.getpid(),
                    foreign_pid,
                    self.path,
                )
            else:
                logger.info(
                    "%s poll lock acquired (pid=%s path=%s)",
                    self.channel,
                    os.getpid(),
                    self.path,
                )
            return True
        except OSError as e:
            logger.warning("%s poll lock acquire failed: %s", self.channel, e)
            if self._fh is not None:
                with contextlib.suppress(OSError):
                    self._fh.close()
                self._fh = None
            # Fail closed on lock errors: better missed poll than dual 409 thrash.
            return False

    def _write_payload(self) -> None:
        if self._fh is None:
            return
        with contextlib.suppress(OSError):
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"{os.getpid()} {time.time():.0f}\n")
            self._fh.flush()

    def heartbeat(self) -> None:
        """Refresh lock timestamp so peers know this poller is still alive."""
        if not self.held or self._fh is None:
            return
        self._write_payload()

    def release(self) -> None:
        if not self.held and self._fh is None:
            return
        try:
            if self._fh is not None:
                if sys.platform == "win32":
                    import msvcrt

                    with contextlib.suppress(OSError):
                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    with contextlib.suppress(OSError):
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    self._fh.close()
                self._fh = None
            if self.path.is_file():
                with contextlib.suppress(OSError):
                    raw = self.path.read_text(encoding="utf-8").strip()
                    if raw.startswith(str(os.getpid())):
                        self.path.unlink(missing_ok=True)
        finally:
            self.held = False
            self.reclaimed = False
            key = self._key()
            if _PROCESS_HOLDERS.get(key) is self:
                _PROCESS_HOLDERS.pop(key, None)


def load_update_offset(home: Path | str | None, channel: str = "telegram") -> int:
    base = Path(home).expanduser() if home else default_home()
    path = base / "locks" / f"{channel}_offset.txt"
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def save_update_offset(home: Path | str | None, offset: int, channel: str = "telegram") -> None:
    if offset <= 0:
        return
    base = Path(home).expanduser() if home else default_home()
    path = base / "locks" / f"{channel}_offset.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(offset)) + "\n", encoding="utf-8")
    except OSError as e:
        logger.debug("save_update_offset failed: %s", e)
