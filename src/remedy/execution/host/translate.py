"""Deterministic POSIX → Windows-cmd rewrite for model-emitted shell strings.

Implemented in Zig (`remedy_core_translate_posix_to_host`,
`remedy_core_looks_like_powershell`, `remedy_core_rewrite_posix_argv`). This
module is the thin binding plus path helpers that supply rg/python/pwsh into
the ABI. No Python rewrite twin — fail closed when Zig is unavailable.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field

from remedy.core.computer import host_binding as hb
from remedy.core.computer.host_binding import HostError
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError


@dataclass
class TranslateResult:
    text: str
    changed: bool = False
    notes: list[str] = field(default_factory=list)
    untranslatable: bool = False
    noop: bool = False


def looks_like_powershell(command: str) -> bool:
    """True when the string is PowerShell, not POSIX/cmd or a script name."""
    return bool(hb.looks_like_powershell(command or ""))


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
    return shutil.which("pwsh") or shutil.which("powershell") or ""


def translate_posix_to_host(
    command: str,
    *,
    host: str | None = None,
    rg_path: str | None = None,
    python_exe: str | None = None,
    pwsh_exe: str | None = None,
) -> TranslateResult:
    """Rewrite POSIX-ish *command* for the host shell via ``remedy_core``.

    *host* defaults to ``cmd`` on Windows and ``posix`` elsewhere. Tests pass
    ``host="cmd"`` to exercise the rewrite table on any OS.
    """
    resolved = host
    if resolved is None:
        resolved = "cmd" if os.name == "nt" else "posix"
    rg = rg_path if rg_path is not None else _find_rg()
    py = python_exe if python_exe is not None else _python_exe()
    pw = pwsh_exe if pwsh_exe is not None else _pwsh_exe()
    data = hb.translate_posix_to_host(
        command or "",
        host=resolved,
        rg_path=rg or None,
        python_exe=py or None,
        pwsh_exe=pw or None,
    )
    notes_raw = data.get("notes") or []
    return TranslateResult(
        text=str(data.get("text") or ""),
        changed=bool(data.get("changed")),
        notes=[str(n) for n in notes_raw] if isinstance(notes_raw, list) else [],
        untranslatable=bool(data.get("untranslatable")),
        noop=bool(data.get("noop")),
    )


def rewrite_posix_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Rewrite a few POSIX argv heads host_run otherwise execs as-is.

    ``host_run(argv=['wc','-l','file'])`` never went through the command-string
    translator, so Windows spent hops on ``'wc' is not recognized``.
    """
    if not argv:
        return argv, []
    try:
        data = hb.rewrite_posix_argv(
            [str(a) for a in argv],
            python_exe=_python_exe() or None,
            pwsh_exe=_pwsh_exe() or None,
        )
    except (HostError, NativeRuntimeUnavailableError):
        raise
    out_raw = data.get("argv") or []
    notes_raw = data.get("notes") or []
    out = [str(a) for a in out_raw] if isinstance(out_raw, list) else list(argv)
    notes = [str(n) for n in notes_raw] if isinstance(notes_raw, list) else []
    return out, notes
