"""meta C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``.
"""
from __future__ import annotations

import ctypes
import json
from collections.abc import Mapping
from ctypes import (
    c_size_t,
)
from typing import Any

from ._core import (
    _BytePtr,
    _check,
    _lib,
    _take,
    _utf8,
)

# ---- ABI 5 additive: diagnose / dialect / stretch --------------------------


def diagnose_host_failure(
    *,
    command: str,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
    translated: str = "",
    timed_out: bool = False,
    host: str = "cmd",
) -> dict[str, Any]:
    """Zig host-failure classifier. Returns a diagnosis dict."""
    payload = _utf8(
        json.dumps(
            {
                "command": command,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": int(exit_code),
                "translated": translated,
                "timed_out": bool(timed_out),
                "host": host or "cmd",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "diagnose_host_failure",
        library.remedy_core_diagnose_host_failure(
            payload, len(payload), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_probe(home: str = "", *, persist: bool = False) -> dict[str, Any]:
    """Zig PATH dialect probe. Optional persist writes dialect.json."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_probe",
        library.remedy_core_dialect_probe(
            home_raw,
            len(home_raw),
            1 if persist else 0,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_load(home: str = "") -> dict[str, Any]:
    """Load dialect.json via Zig (heals empty/sidecar fields)."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_load",
        library.remedy_core_dialect_load(
            home_raw, len(home_raw), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_record_success(
    command: str, *, home: str = "", note: str = ""
) -> dict[str, Any]:
    """Record a successful host command into dialect.json."""
    home_raw = _utf8(home or "")
    cmd_raw = _utf8(command or "")
    note_raw = _utf8(note or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_record_success",
        library.remedy_core_dialect_record_success(
            home_raw,
            len(home_raw),
            cmd_raw,
            len(cmd_raw),
            note_raw,
            len(note_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_format_line(home: str = "", dialect: Mapping[str, Any] | None = None) -> str:
    """One-line host inject from Zig."""
    home_raw = _utf8(home or "")
    dialect_raw = (
        _utf8(json.dumps(dict(dialect), ensure_ascii=False, separators=(",", ":")))
        if dialect is not None
        else b""
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_format_line",
        library.remedy_core_dialect_format_line(
            home_raw,
            len(home_raw),
            dialect_raw,
            len(dialect_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")


def stretch_home(home: str = "", *, force: bool = False) -> dict[str, Any]:
    """Zig first-home stretch; persists home.json (+ dialect)."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_home",
        library.remedy_core_stretch_home(
            home_raw,
            len(home_raw),
            1 if force else 0,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def stretch_load(home: str = "") -> dict[str, Any] | None:
    """Load home.json via Zig; None when missing."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_load",
        library.remedy_core_stretch_load(
            home_raw, len(home_raw), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    raw = _take(library, ptr, length).decode("utf-8")
    if raw.strip() == "null":
        return None
    data = json.loads(raw)
    return dict(data) if isinstance(data, dict) else None


def stretch_needs(home: str = "", *, stale_days: int = 14) -> bool:
    """True when census is missing or older than stale_days."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_needs",
        library.remedy_core_stretch_needs(
            home_raw,
            len(home_raw),
            int(stale_days),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8").strip() == "true"


def stretch_format_line(
    home: str = "", census: Mapping[str, Any] | None = None
) -> str:
    """Compact This-home inject line."""
    home_raw = _utf8(home or "")
    census_raw = (
        _utf8(json.dumps(dict(census), ensure_ascii=False, separators=(",", ":")))
        if census is not None
        else b""
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_format_line",
        library.remedy_core_stretch_format_line(
            home_raw,
            len(home_raw),
            census_raw,
            len(census_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")


def stretch_format_whoami(
    home: str = "", census: Mapping[str, Any] | None = None
) -> str:
    """Longer /whoami and /stretch block."""
    home_raw = _utf8(home or "")
    census_raw = (
        _utf8(json.dumps(dict(census), ensure_ascii=False, separators=(",", ":")))
        if census is not None
        else b""
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_format_whoami",
        library.remedy_core_stretch_format_whoami(
            home_raw,
            len(home_raw),
            census_raw,
            len(census_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")
