"""Zig-backed child handles: HiddenProcess, PipedProcess, wait, retain."""

from __future__ import annotations

import os
import sys
from contextlib import suppress
from typing import Any, TextIO

DEFAULT_RUN_TIMEOUT_S = 120.0

# Retain Zig job/process-group handles so KILL_ON_JOB_CLOSE does not reap
# fire-and-forget launches the moment spawn returns.
_DETACHED_CHILDREN: list[Any] = []


def wrap_os_pipe_handle(os_handle: int, *, writable: bool, text: bool) -> Any:
    """Own *os_handle* (Win32 HANDLE or POSIX fd) as a Python file object."""
    if sys.platform == "win32":
        import msvcrt

        flags = os.O_WRONLY if writable else os.O_RDONLY
        fd = msvcrt.open_osfhandle(int(os_handle), flags)
    else:
        fd = int(os_handle)
    if text:
        mode = "w" if writable else "r"
        return open(  # noqa: SIM115 — caller owns lifetime
            fd,
            mode,
            encoding="utf-8",
            errors="replace",
            buffering=1,
            closefd=True,
        )
    mode_b = "wb" if writable else "rb"
    return open(fd, mode_b, buffering=0, closefd=True)  # noqa: SIM115


class PipedProcess:
    """Authorized 3-pipe child; duck-types the Popen bits voice/bridge needs."""

    def __init__(
        self,
        pid: int,
        handle: int,
        stdin: TextIO | Any,
        stdout: TextIO | Any,
        stderr: TextIO | Any,
    ) -> None:
        self.pid = pid
        self._handle = handle
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.returncode: int | None = None

    def __repr__(self) -> str:
        state = "open" if self._handle else "closed"
        return f"PipedProcess(pid={self.pid}, returncode={self.returncode}, {state})"

    def poll(self) -> int | None:
        return wait_piped(self, 0)

    def wait(self, timeout: float | None = DEFAULT_RUN_TIMEOUT_S) -> int | None:
        return wait_piped(self, timeout)

    def kill(self) -> None:
        from remedy.core.computer import host_binding

        host_binding.process_kill_tree(int(self.pid))

    def terminate(self) -> None:
        self.kill()

    def close(self) -> None:
        from remedy.core.computer import host_binding

        for stream in (self.stdin, self.stdout, self.stderr):
            with suppress(Exception):
                if stream is not None and not getattr(stream, "closed", False):
                    stream.close()
        handle, self._handle = self._handle, 0
        if handle:
            with suppress(Exception):
                host_binding.process_close(handle)

    @property
    def handle(self) -> int:
        return self._handle


class HiddenProcess:
    """Process from authorized spawn_hidden; :meth:`close` ends the job tree."""

    def __init__(self, pid: int, handle: int) -> None:
        self.pid = pid
        self._handle = handle
        self.returncode: int | None = None

    def __repr__(self) -> str:
        state = "open" if self._handle else "closed"
        return f"HiddenProcess(pid={self.pid}, returncode={self.returncode}, {state})"

    def __enter__(self) -> HiddenProcess:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def wait(self, timeout: float | None = DEFAULT_RUN_TIMEOUT_S) -> int | None:
        return wait(self, timeout)

    def poll(self) -> int | None:
        return wait(self, 0)

    def kill_tree(self) -> None:
        from remedy.core.computer import host_binding

        host_binding.process_kill_tree(int(self.pid))

    def terminate(self) -> None:
        """Job/group kill — Zig has no graceful SIGTERM on the spawn handle."""
        self.kill_tree()

    def kill(self) -> None:
        self.kill_tree()

    def close(self) -> None:
        from remedy.core.computer import host_binding

        handle, self._handle = self._handle, 0
        if handle:
            host_binding.process_close(handle)

    @property
    def handle(self) -> int:
        return self._handle


def retain_detached(child: HiddenProcess) -> HiddenProcess:
    """Keep *child*'s job handle alive so KILL_ON_JOB_CLOSE does not reap it."""
    _DETACHED_CHILDREN.append(child)
    return child


def wait(process: HiddenProcess, timeout: float | None = DEFAULT_RUN_TIMEOUT_S) -> int | None:
    """Wait for a :class:`HiddenProcess`; None when *timeout* elapses first."""
    from remedy.core.computer import host_binding

    if not process.handle:
        raise ValueError("HiddenProcess is closed")
    if process.returncode is not None:
        return process.returncode
    timeout_ms = host_binding.WAIT_FOREVER if timeout is None else int(max(0.0, timeout) * 1000)
    code = host_binding.process_wait(process.handle, timeout_ms)
    if code is not None:
        process.returncode = code
    return code


def wait_piped(
    process: PipedProcess, timeout: float | None = DEFAULT_RUN_TIMEOUT_S
) -> int | None:
    """Wait for a :class:`PipedProcess`; None when *timeout* elapses first."""
    from remedy.core.computer import host_binding

    if not process.handle:
        raise ValueError("PipedProcess is closed")
    if process.returncode is not None:
        return process.returncode
    timeout_ms = host_binding.WAIT_FOREVER if timeout is None else int(max(0.0, timeout) * 1000)
    code = host_binding.process_wait(process.handle, timeout_ms)
    if code is not None:
        process.returncode = code
    return code
