"""Legacy CREATE_NO_WINDOW kwargs for pipe-bound spawn sites only.

Production process control lives in :mod:`remedy.execution.process` (Zig
authorized spawn / exec-capture / kill-tree). Soft hide flags remain here for
callers that still need OS pipes until a Zig pipe-spawn ABI exists — do not
grow this module; migrate callers to ``spawn_hidden`` / ``run_hidden`` /
ConPTY instead.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def hidden_creationflags() -> int:
    """Windows CREATE_NO_WINDOW (0 elsewhere)."""
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def hidden_startupinfo() -> Any | None:
    """STARTUPINFO with SW_HIDE alongside CREATE_NO_WINDOW."""
    if sys.platform != "win32":
        return None
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is None:
        return None
    startup = startupinfo_cls()
    startup.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startup.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return startup


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Kwargs for leftover pipe-bound subprocess sites (not Zig-backed)."""
    if sys.platform != "win32":
        return {}
    out: dict[str, Any] = {"creationflags": hidden_creationflags()}
    startup = hidden_startupinfo()
    if startup is not None:
        out["startupinfo"] = startup
    return out
