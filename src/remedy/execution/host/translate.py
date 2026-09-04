"""Deterministic POSIX → Windows-cmd rewrite for model-emitted shell strings.

Implemented in Zig (`remedy_core_translate_posix_to_host`, ABI 4). This module
is the thin binding plus argv-head helpers that never went through the
command-string path. PowerShell payloads are *not* rewritten — the runner
sends them through a temp ``.ps1`` and ``pwsh -File``.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field

# Strong PowerShell-only signals. Do not use POSIX `test -eq` or `start-server`.
# powershell/pwsh are *not* listed here — a later-segment mention ("use powershell")
# must not skip POSIX rewrite. Head match is _PS_HEAD.
_PS_STRONG = re.compile(
    r"(?is)("
    r"\$_\b"
    r"|\$PSVersionTable"
    r"|\$env:[A-Za-z]"
    r"|\bparam\s*\("
    r"|@['\"]"
    r")"
)
# Same idea as scriptfile._PS_WRAPPER: optional path prefix, command head only.
_PS_HEAD = re.compile(
    r"(?is)^\s*(?:(?:[A-Za-z]:\\)?(?:[^\s\"']*[\\/])?)?(?:powershell|pwsh)(?:\.exe)?\b"
)
_PS_CMDLET = re.compile(
    r"(?i)\b(?:Get|Set|New|Remove|Invoke|Write|Select|Where|ForEach|Out|"
    r"Add|Clear|ConvertTo|ConvertFrom|Import|Export|Start|Stop|Test|Measure)"
    r"-([A-Za-z][A-Za-z0-9]+)\b"
)
# Nouns that collide with POSIX / script names (start-server, start-dev).
# "service" is a real PS noun (Get-Service / Start-Service) — do not denylist it.
_PS_FILENAME_NOUNS = frozenset(
    {
        "server",
        "dev",
        "app",
        "all",
        "here",
        "now",
        "script",
        "build",
        "web",
        "api",
    }
)


@dataclass
class TranslateResult:
    text: str
    changed: bool = False
    notes: list[str] = field(default_factory=list)
    untranslatable: bool = False
    noop: bool = False


def looks_like_powershell(command: str) -> bool:
    """True when the string is PowerShell, not POSIX/cmd or a script name."""
    cmd = (command or "").strip()
    if not cmd:
        return False
    if _PS_HEAD.match(cmd):
        return True
    if _PS_STRONG.search(cmd):
        return True
    for m in _PS_CMDLET.finditer(cmd):
        if m.group(1).lower() in _PS_FILENAME_NOUNS:
            continue
        end = m.end()
        trail = cmd[end : end + 8]
        if re.match(r"\.(sh|bash|zsh|py|js|ts|exe|bat|cmd)\b", trail, re.I):
            continue
        return True
    return False


def _q(path: str) -> str:
    """Quote a path for cmd.exe (tests + rewrite_posix_argv helpers)."""
    p = path.replace("/", "\\") if ("/" in path or os.name == "nt") else path
    if not p:
        return '""'
    if len(p) >= 2 and p[0] == p[-1] == '"':
        p = p[1:-1]
    p = p.replace('"', '""')
    return f'"{p}"'


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
    from remedy.core.computer import host_binding as hb

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
    notes: list[str] = []
    head = str(argv[0] or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if head.endswith(".exe"):
        head = head[:-4]
    rest = [str(a) for a in argv[1:]]
    if head == "wc" and any(t in ("-l", "--lines") for t in rest):
        files = [t for t in rest if not t.startswith("-")]
        if files:
            exe = _python_exe()
            win_p = files[0].replace("/", "\\") if ("/" in files[0] or os.name == "nt") else files[0]
            if exe:
                notes.append("wc -l → python line count")
                code = (
                    "p=open(r'''"
                    + win_p.replace("'''", "")
                    + "''',encoding='utf-8',errors='replace').read().splitlines();"
                    + "print(len(p))"
                )
                return [exe, "-c", code], notes
            pw = _pwsh_exe()
            if pw:
                notes.append("wc -l → pwsh Measure-Object")
                ps = (
                    f"(Get-Content -LiteralPath '{files[0].replace(chr(39), chr(39)+chr(39))}' "
                    f"| Measure-Object -Line).Lines"
                )
                return [pw, "-NoProfile", "-Command", ps], notes
            notes.append(
                "wc -l needs Python — install Python 3 or set REMEDY_PYTHON to python.exe"
            )
            return argv, notes
    return argv, notes
