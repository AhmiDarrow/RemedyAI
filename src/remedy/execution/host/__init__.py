"""Remedy Host Bridge — dialect-aware adaptor between the model and the OS.

Models emit POSIX/bash. Windows is cmd + two PowerShells. Zig ``remedy_core``
owns prepare/translate/scriptfile/ConPTY/HostSession/diagnose/dialect/stretch;
this package keeps thin bindings only (no Python twins, no soft fallbacks).
Diagnose/dialect/stretch callers use ``host_binding`` directly.
"""

from __future__ import annotations

from remedy.execution.host.ir import HostOp, mkdir_op, raw_op, run_op, script_op, which_op
from remedy.execution.host.runner import (
    PreparedCommand,
    coerce_argv,
    prepare_host_command,
    prepare_host_op,
    resolve_which,
)
from remedy.execution.host.session import (
    HostSession,
    SessionResult,
    close_all_shared_sessions,
    close_shared_session,
    conpty_available,
    get_shared_session,
)
from remedy.execution.host.translate import looks_like_powershell, translate_posix_to_host

__all__ = [
    "HostOp",
    "HostSession",
    "PreparedCommand",
    "SessionResult",
    "close_all_shared_sessions",
    "close_shared_session",
    "coerce_argv",
    "conpty_available",
    "get_shared_session",
    "looks_like_powershell",
    "mkdir_op",
    "prepare_host_command",
    "prepare_host_op",
    "raw_op",
    "resolve_which",
    "run_op",
    "script_op",
    "translate_posix_to_host",
    "which_op",
]
