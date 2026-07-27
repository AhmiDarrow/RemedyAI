"""Exclusive long-poll ownership so only one Remedy process polls a messenger bot.

Telegram returns HTTP 409 when two getUpdates pollers share a bot token — realtime
dies for the loser and feels like "sync stuck". Hold a PID lock file under
``~/.remedy/locks/``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # Windows PROCESS_QUERY_LIMITED_INFORMATION
            access = 0x1000
            handle = kernel32.OpenProcess(access, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class MessengerPollLock:
    """Non-blocking exclusive lock for one messenger channel poller."""

    def __init__(self, home: Path | str | None, channel: str = "telegram") -> None:
        base = Path(home).expanduser() if home else Path.home() / ".remedy"
        self.path = base / "locks" / f"{channel}_getupdates.lock"
        self.channel = channel
        self._fh = None  # keep open so Windows exclusive share holds
        self.held = False

    def try_acquire(self) -> bool:
        """Return True if this process owns the poller. False → do not start poll loop."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("%s poll lock mkdir failed: %s", self.channel, e)
            return True  # fail open so a single broken FS path still works

        # Stale lock: previous owner dead — remove so we can take over cleanly.
        if self.path.is_file():
            try:
                raw = self.path.read_text(encoding="utf-8").strip()
                old_pid = int(raw.split()[0])
                if old_pid != os.getpid() and _pid_alive(old_pid):
                    logger.warning(
                        "%s poll lock held by live pid=%s — this process will not "
                        "long-poll (avoid HTTP 409). Quit the other Remedy instance "
                        "or wait for it to exit.",
                        self.channel,
                        old_pid,
                    )
                    return False
                if old_pid != os.getpid() and not _pid_alive(old_pid):
                    with contextlib.suppress(OSError):
                        self.path.unlink(missing_ok=True)
            except (OSError, ValueError, IndexError):
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
                    logger.warning(
                        "%s poll lock busy (fcntl) — not starting long-poll",
                        self.channel,
                    )
                    return False

            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"{os.getpid()} {time.time():.0f}\n")
            self._fh.flush()
            self.held = True
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


def load_update_offset(home: Path | str | None, channel: str = "telegram") -> int:
    base = Path(home).expanduser() if home else Path.home() / ".remedy"
    path = base / "locks" / f"{channel}_offset.txt"
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def save_update_offset(home: Path | str | None, offset: int, channel: str = "telegram") -> None:
    if offset <= 0:
        return
    base = Path(home).expanduser() if home else Path.home() / ".remedy"
    path = base / "locks" / f"{channel}_offset.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(offset)) + "\n", encoding="utf-8")
    except OSError as e:
        logger.debug("save_update_offset failed: %s", e)
