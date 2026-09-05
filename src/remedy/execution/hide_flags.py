"""Retired — soft CREATE_NO_WINDOW kwargs are gone from production.

Zig owns process spawn (``spawn_hidden`` / ``run_hidden`` / kill-tree).
Interactive pipe leftovers fail closed via ``HostError`` in
:mod:`remedy.execution.process`. Test-only soft helpers live in
``tests.harness.process_soft``.
"""

from __future__ import annotations

from typing import Any, NoReturn


def _retired(helper: str) -> NoReturn:
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError
    from remedy.execution.process import require_process_host

    require_process_host()
    raise HostError(helper, STATUS_UNSUPPORTED)


def hidden_creationflags() -> int:
    """Fail closed — use :func:`remedy.execution.process.spawn_hidden`."""
    _retired("hidden_creationflags")


def hidden_startupinfo() -> Any | None:
    """Fail closed — use :func:`remedy.execution.process.spawn_hidden`."""
    _retired("hidden_startupinfo")


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Fail closed — no soft pipe hide kwargs in production."""
    _retired("hidden_subprocess_kwargs")
