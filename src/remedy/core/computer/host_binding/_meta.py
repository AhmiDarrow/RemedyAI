"""meta C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``. Also owns dialect-backed
``resolve_which`` / ``default_script_lang`` (Zig dialect + host_op_prepare).
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Mapping
from ctypes import (
    c_size_t,
)
from pathlib import Path
from typing import Any

from ._core import (
    _BytePtr,
    _check,
    _lib,
    _take,
    _utf8,
)
from ._json import take_json

# ---- ABI 5 additive: diagnose / dialect / stretch --------------------------


def _take_dict(library: Any, ptr: Any, length: Any) -> dict[str, Any]:
    data = take_json(library, ptr, length)
    return dict(data) if isinstance(data, dict) else {}


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
    return _take_dict(library, ptr, length)


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
    return _take_dict(library, ptr, length)


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
    return _take_dict(library, ptr, length)


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
    return _take_dict(library, ptr, length)


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
    return _take_dict(library, ptr, length)


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
    data = take_json(library, ptr, length)
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


def _ok_python(path: str | None) -> bool:
    if not path:
        return False
    from remedy.core.build_python import is_usable_host_python

    return is_usable_host_python(path)


def resolve_which(name: str, *, cwd: Path | str | None = None) -> str | None:
    """Resolve an executable via project bins, Zig dialect, then host_op_prepare.

    No ``shutil.which`` soft twin — native runtime errors fail closed.
    """
    from ._ir import host_op_prepare

    n = (name or "").strip()
    if not n:
        return None
    key = n.lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if key.endswith(".exe"):
        key = key[:-4]

    if cwd is not None:
        try:
            from remedy.core.project_fingerprint import local_bin_dirs

            suffix = ".exe" if os.name == "nt" else ""
            for bin_dir in local_bin_dirs(cwd):
                cand = bin_dir / (
                    n + suffix if suffix and not n.lower().endswith(suffix) else n
                )
                if cand.is_file():
                    return str(cand)
                if suffix:
                    alt = bin_dir / f"{key}{suffix}"
                    if alt.is_file():
                        return str(alt)
        except OSError:
            pass

    d = dialect_load("")
    mapped = {
        "python": str(d.get("python_cmd") or ""),
        "python3": str(d.get("python_cmd") or ""),
        "py": str(d.get("python_cmd") or ""),
        "git": str(d.get("git_cmd") or ""),
        "rg": str(d.get("rg_cmd") or ""),
        "pwsh": str(d.get("pwsh_cmd") or ""),
    }
    hit = (mapped.get(key) or "").strip()
    if hit and Path(hit).is_file():
        if key in {"python", "python3", "py"}:
            if _ok_python(hit):
                return hit
        else:
            return hit

    prep = host_op_prepare(
        op={"kind": "run", "argv": [n]},
        project_path=str(cwd) if cwd is not None else None,
    )
    argv = prep.get("argv") or []
    if argv:
        prepared = str(argv[0] or "")
        prepared_path = Path(prepared)
        if prepared and prepared_path.is_file() and prepared_path.is_absolute():
            if key in {"python", "python3", "py"}:
                if _ok_python(prepared):
                    return prepared
            else:
                return prepared

    if key in {"python", "python3"}:
        try:
            from remedy.core.build_python import host_python_executable

            found = host_python_executable()
        except OSError:
            found = ""
        if found and _ok_python(found):
            return found
        from remedy.core.runtime_identity import is_frozen_install

        if is_frozen_install():
            return None
        exe = sys.executable or ""
        return exe if _ok_python(exe) else None
    return None


def default_script_lang(home: str | Path | None = None) -> str:
    """pwsh when Zig dialect finds it; otherwise python (POSIX) or cmd."""
    if os.name != "nt":
        return "python"
    d = dialect_load(str(home) if home else "")
    pwsh = str(d.get("pwsh_cmd") or "").strip()
    if pwsh and Path(pwsh).is_file():
        return "pwsh"
    return "cmd"
