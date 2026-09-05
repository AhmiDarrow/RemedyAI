"""ir C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Mapping, Sequence
from ctypes import (
    c_size_t,
    c_uint8,
)
from typing import Any

from ._core import (
    STATUS_OPERATION_FAILED,
    HostError,
    _BytePtr,
    _check,
    _lib,
    _utf8,
)
from ._json import take_json

# --- Host Command IR prepare (ABI 4) -----------------------------------------


def host_op_prepare(
    op: Mapping[str, Any] | None = None,
    *,
    scratch_dir: str | None = None,
    project_path: str | None = None,
    raw: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Call ``remedy_core_host_op_prepare``; return a PreparedCommand dict.

    Pass either a HostOp mapping as *op*, or a full request object as *raw*
    (``{op, scratch_dir?, project_path?}``, ``{command, host?, ...}``, or a
    bare HostOp). :func:`remedy.core.computer.shell_host.prepare_host_op` and
    :func:`remedy.core.computer.shell_host.prepare_host_command` both route here.
    """
    if raw is not None:
        payload: dict[str, Any] = dict(raw)
    else:
        if not isinstance(op, Mapping):
            raise TypeError("host_op_prepare requires op= or raw=")
        payload = {"op": dict(op)}
        if scratch_dir:
            payload["scratch_dir"] = scratch_dir
        if project_path:
            payload["project_path"] = project_path
    encoded = _utf8(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_op_prepare",
        library.remedy_core_host_op_prepare(
            encoded, len(encoded), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    result = take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("host_op_prepare", STATUS_OPERATION_FAILED)
    return result


def _find_rg() -> str:
    """Supply ``rg_path`` to Zig; monkeypatchable in tests."""
    try:
        from remedy.core.rg_binary import find_rg

        path, _src = find_rg()
        return str(path) if path else ""
    except Exception:
        return ""


def _python_exe() -> str:
    """A real CPython for head/tail/wc rewrites passed into Zig."""
    try:
        from remedy.core.build_python import host_python_executable

        found = host_python_executable()
        if found:
            return found
    except Exception:
        pass
    from remedy.core.runtime_identity import is_frozen_install

    if is_frozen_install():
        return ""
    from remedy.core.build_python import is_usable_host_python

    exe = sys.executable or ""
    if exe and is_usable_host_python(exe):
        return exe
    return ""


def _pwsh_exe() -> str:
    """PowerShell for line rewrites when no CPython exists."""
    import shutil

    return shutil.which("pwsh") or shutil.which("powershell") or ""


def translate_posix_to_host(
    command: str,
    *,
    host: str | None = None,
    rg_path: str | None = None,
    python_exe: str | None = None,
    pwsh_exe: str | None = None,
) -> dict[str, Any]:
    """Call ``remedy_core_translate_posix_to_host``; return a TranslateResult dict.

    When *rg_path* / *python_exe* / *pwsh_exe* are omitted (``None``), path
    helpers fill them for Zig. Pass ``""`` to force empty.
    """
    if host is None:
        host = "cmd" if os.name == "nt" else "posix"
    # Resolve via package so tests can monkeypatch host_binding._find_rg / etc.
    from remedy.core.computer import host_binding as api

    rg = api._find_rg() if rg_path is None else rg_path
    py = api._python_exe() if python_exe is None else python_exe
    pw = api._pwsh_exe() if pwsh_exe is None else pwsh_exe
    payload: dict[str, Any] = {"command": command or "", "host": host}
    if rg:
        payload["rg_path"] = rg
    if py:
        payload["python_exe"] = py
    if pw:
        payload["pwsh_exe"] = pw
    encoded = _utf8(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "translate_posix_to_host",
        library.remedy_core_translate_posix_to_host(
            encoded, len(encoded), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    result = take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("translate_posix_to_host", STATUS_OPERATION_FAILED)
    return result


def looks_like_powershell(command: str) -> bool:
    """True when Zig classifies *command* as PowerShell (not POSIX/cmd)."""
    library = _lib()
    raw = _utf8(command or "")
    flag = c_uint8()
    _check(
        library,
        "looks_like_powershell",
        library.remedy_core_looks_like_powershell(
            raw, len(raw), ctypes.byref(flag)
        ),
    )
    return bool(flag.value)


def rewrite_posix_argv(
    argv: Sequence[str],
    *,
    python_exe: str | None = None,
    pwsh_exe: str | None = None,
) -> dict[str, Any]:
    """Call ``remedy_core_rewrite_posix_argv``; return ``{argv, notes}``.

    Omitting *python_exe* / *pwsh_exe* fills them via path helpers.
    """
    from remedy.core.computer import host_binding as api

    py = api._python_exe() if python_exe is None else python_exe
    pw = api._pwsh_exe() if pwsh_exe is None else pwsh_exe
    payload: dict[str, Any] = {"argv": [str(a) for a in argv]}
    if py:
        payload["python_exe"] = py
    if pw:
        payload["pwsh_exe"] = pw
    encoded = _utf8(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "rewrite_posix_argv",
        library.remedy_core_rewrite_posix_argv(
            encoded, len(encoded), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    result = take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("rewrite_posix_argv", STATUS_OPERATION_FAILED)
    return result
