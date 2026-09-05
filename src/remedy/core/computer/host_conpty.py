"""Windows ConPTY spawn — thin async facade over Zig ``host_binding`` (ABI 5).

Duck-type process/stream live in ``host_binding._conpty``. Spawn/IO failures
raise (no soft Python ConPTY twin and no unsigned-spawn fallback).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from remedy.core.computer import host_binding
from remedy.core.computer.host_binding import (
    HostError,
    _ConPTYProcess,
    _HandleStream,
    spawn_conpty_process,
)
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

# Compat alias — tests and callers patch/call the sync spawn by this name.
_spawn_conpty_sync = spawn_conpty_process

__all__ = [
    "_ConPTYProcess",
    "_HandleStream",
    "_spawn_conpty_sync",
    "spawn_conpty",
    "spawn_conpty_supported",
]


def spawn_conpty_supported() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(host_binding.conpty_available())
    except (HostError, NativeRuntimeUnavailableError, OSError):
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
