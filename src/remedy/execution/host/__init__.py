"""Remedy Host Bridge — dialect-aware adaptor between the model and the OS.

Models emit POSIX/bash. Windows is cmd + two PowerShells. This package owns
that gap: structured ops, POSIX rewrite, ``pwsh -File`` (never ``-Command``),
teach-back errors, last-good dialect, optional persistent session.
"""

from __future__ import annotations

from remedy.execution.host.diagnose import HostDiagnosis, diagnose_host_failure
from remedy.execution.host.dialect import (
    HostDialect,
    format_dialect_line,
    load_dialect,
    probe_host_dialect,
    record_success,
    save_dialect,
)
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
    close_shared_session,
    conpty_available,
    get_shared_session,
)
from remedy.execution.host.stretch import (
    HomeCensus,
    ensure_home_stretch,
    format_home_line,
    format_home_whoami,
    load_census,
    needs_stretch,
    stretch_home,
)
from remedy.execution.host.translate import looks_like_powershell, translate_posix_to_host

__all__ = [
    "HomeCensus",
    "HostDiagnosis",
    "HostDialect",
    "HostOp",
    "HostSession",
    "PreparedCommand",
    "SessionResult",
    "close_shared_session",
    "coerce_argv",
    "conpty_available",
    "diagnose_host_failure",
    "ensure_home_stretch",
    "format_dialect_line",
    "format_home_line",
    "format_home_whoami",
    "get_shared_session",
    "load_census",
    "load_dialect",
    "looks_like_powershell",
    "mkdir_op",
    "needs_stretch",
    "prepare_host_command",
    "prepare_host_op",
    "probe_host_dialect",
    "raw_op",
    "record_success",
    "resolve_which",
    "run_op",
    "save_dialect",
    "script_op",
    "stretch_home",
    "translate_posix_to_host",
    "which_op",
]
