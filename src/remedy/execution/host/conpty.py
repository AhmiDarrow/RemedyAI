"""Windows ConPTY spawn — child sees a real console; parent still uses pipes.

Used by the persistent host session when ``use_conpty=True``. Any API failure
raises; the session falls back to hidden pipes.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any

_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400


def spawn_conpty_supported() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return hasattr(k32, "CreatePseudoConsole")
    except Exception:
        return False


async def spawn_conpty(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    """Return a duck-typed process with async stdin/stdout (ConPTY attached)."""
    override = getattr(spawn_conpty, "_override", None)
    if override is not None:
        return await override(argv, cwd=cwd, env=env)
    if sys.platform != "win32":
        raise RuntimeError("ConPTY is Windows-only")
    return await asyncio.to_thread(_spawn_conpty_sync, argv, cwd, env)


def _spawn_conpty_sync(
    argv: list[str],
    cwd: str | None,
    env: dict[str, str] | None,
) -> _ConPTYProcess:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class StartupInfoW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class StartupInfoExW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", StartupInfoW),
            ("lpAttributeList", ctypes.c_void_p),
        ]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    k32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(SecurityAttributes),
        wintypes.DWORD,
    ]
    k32.CreatePipe.restype = wintypes.BOOL
    k32.CreatePseudoConsole.argtypes = [
        COORD,
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    k32.CreatePseudoConsole.restype = ctypes.HRESULT
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    k32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    k32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(ProcessInformation),
    ]
    k32.CreateProcessW.restype = wintypes.BOOL

    sa = SecurityAttributes()
    sa.nLength = ctypes.sizeof(SecurityAttributes)
    sa.lpSecurityDescriptor = None
    sa.bInheritHandle = False

    h_pty_in = wintypes.HANDLE()
    h_con_in = wintypes.HANDLE()  # parent writes
    if not k32.CreatePipe(ctypes.byref(h_pty_in), ctypes.byref(h_con_in), ctypes.byref(sa), 0):
        raise OSError("CreatePipe input failed")

    h_con_out = wintypes.HANDLE()  # parent reads
    h_pty_out = wintypes.HANDLE()
    if not k32.CreatePipe(ctypes.byref(h_con_out), ctypes.byref(h_pty_out), ctypes.byref(sa), 0):
        k32.CloseHandle(h_pty_in)
        k32.CloseHandle(h_con_in)
        raise OSError("CreatePipe output failed")

    h_pc = ctypes.c_void_p()
    hr = k32.CreatePseudoConsole(COORD(120, 40), h_pty_in, h_pty_out, 0, ctypes.byref(h_pc))
    if hr != 0:
        for h in (h_pty_in, h_con_in, h_con_out, h_pty_out):
            k32.CloseHandle(h)
        raise OSError(f"CreatePseudoConsole failed hr={hr}")

    # Parent does not need the pty-side pipe ends.
    k32.CloseHandle(h_pty_in)
    k32.CloseHandle(h_pty_out)

    size = ctypes.c_size_t(0)
    k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attr_buf = (ctypes.c_char * size.value)()
    if not k32.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(size)):
        k32.CloseHandle(h_con_in)
        k32.CloseHandle(h_con_out)
        k32.ClosePseudoConsole(h_pc)
        raise OSError("InitializeProcThreadAttributeList failed")

    if not k32.UpdateProcThreadAttribute(
        attr_buf,
        0,
        _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
        h_pc,
        ctypes.sizeof(ctypes.c_void_p),
        None,
        None,
    ):
        k32.DeleteProcThreadAttributeList(attr_buf)
        k32.CloseHandle(h_con_in)
        k32.CloseHandle(h_con_out)
        k32.ClosePseudoConsole(h_pc)
        raise OSError("UpdateProcThreadAttribute failed")

    siex = StartupInfoExW()
    siex.StartupInfo.cb = ctypes.sizeof(StartupInfoExW)
    siex.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

    cmdline = subprocess.list2cmdline(argv)
    env_block = _env_block(env)
    pi = ProcessInformation()
    created = k32.CreateProcessW(
        None,
        ctypes.c_wchar_p(cmdline),
        None,
        None,
        False,
        _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
        env_block,
        cwd,
        ctypes.byref(siex),
        ctypes.byref(pi),
    )
    k32.DeleteProcThreadAttributeList(attr_buf)
    if not created:
        err = ctypes.get_last_error()
        k32.CloseHandle(h_con_in)
        k32.CloseHandle(h_con_out)
        k32.ClosePseudoConsole(h_pc)
        raise OSError(f"CreateProcessW failed winerr={err}")

    k32.CloseHandle(pi.hThread)
    return _ConPTYProcess(
        pid=int(pi.dwProcessId),
        process_handle=int(pi.hProcess),
        stdin_handle=int(h_con_in),
        stdout_handle=int(h_con_out),
        pc_handle=int(h_pc) if h_pc.value else 0,
    )


def _env_block(env: dict[str, str] | None) -> Any:
    import ctypes

    if env is None:
        return None
    parts = [f"{k}={v}" for k, v in env.items()]
    blob = "\0".join(parts) + "\0\0"
    return ctypes.create_unicode_buffer(blob)


class _HandleStream:
    def __init__(self, handle: int, *, write: bool) -> None:
        self._handle = handle
        self._write = write
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed or not self._write:
            return
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        written = wintypes.DWORD(0)
        buf = ctypes.create_string_buffer(data)
        k32.WriteFile(self._handle, buf, len(data), ctypes.byref(written), None)

    async def drain(self) -> None:
        return None

    async def read(self, n: int = 4096) -> bytes:
        if self._closed or self._write:
            return b""
        return await asyncio.to_thread(self._read_sync, n)

    def _read_sync(self, n: int) -> bytes:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buf = ctypes.create_string_buffer(max(1, n))
        got = wintypes.DWORD(0)
        ok = k32.ReadFile(self._handle, buf, max(1, n), ctypes.byref(got), None)
        if not ok or got.value == 0:
            return b""
        return buf.raw[: got.value]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
        except Exception:
            pass


class _ConPTYProcess:
    """Duck-type asyncio.subprocess.Process for HostSession."""

    def __init__(
        self,
        *,
        pid: int,
        process_handle: int,
        stdin_handle: int,
        stdout_handle: int,
        pc_handle: int,
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = _HandleStream(stdin_handle, write=True)
        self.stdout = _HandleStream(stdout_handle, write=False)
        self.stderr = None
        self._ph = process_handle
        self._pc = pc_handle

    def kill(self) -> None:
        self._terminate()

    def terminate(self) -> None:
        self._terminate()

    def _terminate(self) -> None:
        try:
            import ctypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.TerminateProcess(self._ph, 1)
        except Exception:
            pass
        self._close()
        self.returncode = 1

    def _close(self) -> None:
        from contextlib import suppress

        with suppress(Exception):
            self.stdin.close()
        with suppress(Exception):
            self.stdout.close()
        with suppress(Exception):
            import ctypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if self._pc:
                k32.ClosePseudoConsole(self._pc)
            if self._ph:
                k32.CloseHandle(self._ph)
        self._pc = 0
        self._ph = 0
