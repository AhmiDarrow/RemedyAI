"""Pure argv helpers for authorized spawn (no Zig / soft subprocess)."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from pathlib import Path


def resolve_argv0(argv: Sequence[str]) -> list[str]:
    """Resolve argv[0] to an absolute path (required by authorized spawn)."""
    args = [str(a) for a in argv]
    if not args:
        raise ValueError("argv must not be empty")
    exe = args[0]
    path = Path(exe)
    if path.is_absolute():
        return args
    found = shutil.which(exe)
    if found is None:
        raise FileNotFoundError(exe)
    args[0] = str(Path(found).resolve())
    return args


def win_shell_prefix() -> list[str]:
    """Argv prefix for a shell command string (cmd /c on Windows, sh -c elsewhere)."""
    if sys.platform != "win32":
        sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        return [sh, "-c"]
    cmd = shutil.which("cmd") or "cmd.exe"
    return [cmd, "/c"]
