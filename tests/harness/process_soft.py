"""Test-only soft subprocess helpers — never imported by production code.

Production ``remedy.execution.process`` fail-closes piped spawn on every
platform (Zig has no interactive pipe-spawn). Tests that need a controllable
CREATE_NO_WINDOW Popen / merge of creation flags use this double instead of
reintroducing a production soft fallback (NATIVE_CUTOVER Rule 5).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any


def soft_hidden_subprocess_kwargs() -> dict[str, Any]:
    """Mirror of legacy hide kwargs for tests that assert flag merge."""
    if sys.platform != "win32":
        return {}
    out: dict[str, Any] = {
        "creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    }
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startup = startupinfo_cls()
        startup.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startup.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        out["startupinfo"] = startup
    return out


def soft_merge_hidden(extra: dict[str, Any]) -> dict[str, Any]:
    """OR caller creationflags into soft hide kwargs (test double only)."""
    kwargs = soft_hidden_subprocess_kwargs()
    flags = extra.pop("creationflags", 0)
    if flags:
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | int(flags)
    kwargs.update(extra)
    return kwargs


def soft_popen_hidden(
    args: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    **extra: Any,
) -> subprocess.Popen[Any]:
    """Soft Popen with hide kwargs — tests only."""
    return subprocess.Popen(
        list(args),
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        **soft_merge_hidden(dict(extra)),
    )
