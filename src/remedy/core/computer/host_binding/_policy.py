"""policy C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from ctypes import (
    c_size_t,
    c_uint8,
    c_uint32,
    c_uint64,
)
from typing import Any, NamedTuple

from remedy.interfaces.secret_store import (
    decode_host_signing_key,
    encode_host_signing_key,
    load_or_create_host_signing_key,
)
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

from ._core import (
    STATUS_OPERATION_FAILED,
    STATUS_UNSUPPORTED,
    HostError,
    _BytePtr,
    _check,
    _lib,
    _take,
    _utf8,
)
from ._json import take_json

# --- ABI 5 additive: policy + capability tokens ------------------------------

CAPABILITY_TOKEN_SIZE = 169
PROCESS_SPAWN_RIGHT = 1 << 2
OWNER_CHECKPOINT_RIGHT = 1 << 5
DEFAULT_SPAWN_SUBJECT = "agent:remedy"
DEFAULT_SPAWN_SCOPE = "workspace:local"
_TOKEN_LIFETIME_MS = 60_000

_signing_key_ready = False

# Re-export secret-store helpers under the historical host_binding names.
_encode_host_signing_key = encode_host_signing_key
_decode_host_signing_key = decode_host_signing_key


def security_set_signing_key(key: bytes | bytearray | memoryview) -> None:
    """Install the HMAC signing key (first 32 bytes). Test or secret-store material only."""
    global _signing_key_ready
    raw = bytes(key)
    if len(raw) < 32:
        raise ValueError("signing key must be at least 32 bytes")
    library = _lib()
    buf = (c_uint8 * 32).from_buffer_copy(raw[:32])
    _check(
        library,
        "security_set_signing_key",
        library.remedy_core_security_set_signing_key(buf, 32),
    )
    _set_signing_ready(True)


def security_clear_signing_key() -> None:
    library = _lib()
    _check(library, "security_clear_signing_key", library.remedy_core_security_clear_signing_key())
    _set_signing_ready(False)


def _set_signing_ready(ready: bool) -> None:
    """Keep package + module flags aligned for monkeypatchable tests."""
    global _signing_key_ready
    _signing_key_ready = bool(ready)
    pkg = sys.modules.get("remedy.core.computer.host_binding")
    if pkg is not None:
        pkg.__dict__["_signing_key_ready"] = _signing_key_ready


def ensure_spawn_signing_key() -> None:
    """Ensure the Zig HMAC signing key is installed from the secret store."""
    pkg = sys.modules.get("remedy.core.computer.host_binding")
    if (pkg is not None and getattr(pkg, "_signing_key_ready", False)) or _signing_key_ready:
        _set_signing_ready(True)
        return
    env_key = os.environ.get("REMEDY_SPAWN_SIGNING_KEY", "")
    if env_key:
        raw = (
            bytes.fromhex(env_key)
            if all(c in "0123456789abcdefABCDEF" for c in env_key) and len(env_key) >= 64
            else env_key.encode("utf-8")
        )
    else:
        raw = load_or_create_host_signing_key()
    security_set_signing_key(raw)


def policy_hash_argv(argv: Sequence[str]) -> bytes:
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    out = (c_uint8 * 32)()
    _check(
        library,
        "policy_hash_argv",
        library.remedy_core_policy_hash_argv(argv_raw, len(argv_raw), out, 32),
    )
    return bytes(out)


def capability_issue(
    *,
    operation_hash: bytes,
    rights_bits: int = PROCESS_SPAWN_RIGHT,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    issued_at_ms: int | None = None,
    expires_at_ms: int | None = None,
    nonce: bytes | None = None,
) -> bytes:
    """Issue a v2 capability token bound to *operation_hash* (32 bytes)."""
    ensure_spawn_signing_key()
    if len(operation_hash) != 32:
        raise ValueError("operation_hash must be 32 bytes")
    now = int(time.time() * 1000) if issued_at_ms is None else int(issued_at_ms)
    exp = now + _TOKEN_LIFETIME_MS if expires_at_ms is None else int(expires_at_ms)
    nonce_raw = os.urandom(16) if nonce is None else bytes(nonce)
    if len(nonce_raw) != 16:
        raise ValueError("nonce must be 16 bytes")
    library = _lib()
    op_buf = (c_uint8 * 32).from_buffer_copy(operation_hash)
    nonce_buf = (c_uint8 * 16).from_buffer_copy(nonce_raw)
    out = (c_uint8 * CAPABILITY_TOKEN_SIZE)()
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    _check(
        library,
        "capability_issue",
        library.remedy_core_capability_issue(
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            op_buf,
            32,
            int(rights_bits),
            now,
            exp,
            nonce_buf,
            16,
            out,
            CAPABILITY_TOKEN_SIZE,
        ),
    )
    return bytes(out)


def issue_process_spawn_token(
    argv: Sequence[str],
    *,
    owner_checkpoint: bool = False,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
) -> tuple[bytes, int]:
    """Return ``(token, now_ms)`` for an authorized spawn of *argv*."""
    digest = policy_hash_argv(argv)
    now = int(time.time() * 1000)
    rights = PROCESS_SPAWN_RIGHT
    if owner_checkpoint:
        rights |= OWNER_CHECKPOINT_RIGHT
    token = capability_issue(
        operation_hash=digest,
        rights_bits=rights,
        subject=subject,
        scope=scope,
        issued_at_ms=now,
        expires_at_ms=now + _TOKEN_LIFETIME_MS,
    )
    return token, now


def write_jail_set_roots(roots: Sequence[str] | None) -> None:
    """Install write roots for authorized spawn (empty / None = Full, no workdir jail)."""
    library = _lib()
    payload = _utf8(json.dumps([str(r) for r in (roots or [])]))
    _check(
        library,
        "write_jail_set_roots",
        library.remedy_core_write_jail_set_roots(payload, len(payload)),
    )


def write_jail_clear() -> None:
    library = _lib()
    _check(library, "write_jail_clear", library.remedy_core_write_jail_clear())


def write_jail_check_path(path: str, cwd: str | None = None) -> None:
    """Raise :class:`HostError` with ACCESS_DENIED when *path* escapes the jail."""
    library = _lib()
    path_raw = _utf8(str(path))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    _check(
        library,
        "write_jail_check_path",
        library.remedy_core_write_jail_check_path(
            path_raw, len(path_raw), cwd_raw, len(cwd_raw)
        ),
    )


def write_jail_check_spawn(argv: Sequence[str], cwd: str | None = None) -> None:
    """Raise :class:`HostError` when argv/cwd would be denied by the write jail."""
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    _check(
        library,
        "write_jail_check_spawn",
        library.remedy_core_write_jail_check_spawn(
            argv_raw, len(argv_raw), cwd_raw, len(cwd_raw)
        ),
    )


def shell_chain_expand(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Call ``remedy_core_shell_chain_expand``; return ``{hops: null|list}``.

    Missing symbol → :class:`HostError`.
    """
    encoded = _utf8(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
    try:
        library = _lib()
        fn = library.remedy_core_shell_chain_expand
    except (AttributeError, NativeRuntimeUnavailableError) as exc:
        raise HostError("shell_chain_expand", STATUS_UNSUPPORTED) from exc
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "shell_chain_expand",
        fn(encoded, len(encoded), ctypes.byref(ptr), ctypes.byref(length)),
    )
    result = take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("shell_chain_expand", STATUS_OPERATION_FAILED)
    return result


def shell_chain_execute(
    payload: Mapping[str, Any],
    *,
    abort_flag: c_uint8 | None = None,
) -> dict[str, Any]:
    """Call ``remedy_core_shell_chain_execute``; return the execute result dict.

    *abort_flag* is an optional ``ctypes.c_uint8`` polled by Zig (non-zero aborts).
    Missing symbol → :class:`HostError`.
    """
    encoded = _utf8(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
    try:
        library = _lib()
        fn = library.remedy_core_shell_chain_execute
    except (AttributeError, NativeRuntimeUnavailableError) as exc:
        raise HostError("shell_chain_execute", STATUS_UNSUPPORTED) from exc
    ptr, length = _BytePtr(), c_size_t()
    flag_arg = ctypes.byref(abort_flag) if abort_flag is not None else None
    _check(
        library,
        "shell_chain_execute",
        fn(
            encoded,
            len(encoded),
            flag_arg,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    result = take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("shell_chain_execute", STATUS_OPERATION_FAILED)
    return result


def process_spawn_authorized(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    token: bytes,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    owner_confirmed: bool = False,
    now_ms: int | None = None,
    write_roots: Sequence[str] | None = None,
) -> tuple[int, int]:
    """Authorized hidden spawn. *argv[0]* must be absolute. No unsigned fallback.

    When *write_roots* is not ``None``, installs those roots for the jail check
    on this spawn (empty sequence = Full / unbound). ``None`` leaves the
    previously installed roots unchanged.
    """
    if write_roots is not None:
        write_jail_set_roots(write_roots)
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = _utf8(json.dumps({str(k): str(v) for k, v in env.items()})) if env is not None else b""
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    token_raw = bytes(token)
    pid, handle = c_uint32(), c_uint64()
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    _check(
        library,
        "process_spawn_authorized",
        library.remedy_core_process_spawn_authorized(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            ctypes.byref(pid),
            ctypes.byref(handle),
        ),
    )
    return pid.value, handle.value


class PipedSpawnResult(NamedTuple):
    """Outcome of :func:`process_spawn_piped_authorized` (OS handles, not files)."""

    pid: int
    handle: int
    stdin_write: int
    stdout_read: int
    stderr_read: int


def process_spawn_piped_authorized(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    token: bytes,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    owner_confirmed: bool = False,
    now_ms: int | None = None,
    write_roots: Sequence[str] | None = None,
) -> PipedSpawnResult:
    """Authorized interactive 3-pipe spawn. *argv[0]* must be absolute.

    Returns parent OS handles (Windows ``HANDLE`` or POSIX fd as ``int``).
    Caller owns the three pipe ends; ``process_close(handle)`` does not close
    them. No unsigned soft fallback. *write_roots* semantics match
    :func:`process_spawn_authorized`.
    """
    if write_roots is not None:
        write_jail_set_roots(write_roots)
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = (
        _utf8(json.dumps({str(k): str(v) for k, v in env.items()}))
        if env is not None
        else b""
    )
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    token_raw = bytes(token)
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    pid, handle = c_uint32(), c_uint64()
    stdin_h, stdout_h, stderr_h = c_uint64(), c_uint64(), c_uint64()
    _check(
        library,
        "process_spawn_piped_authorized",
        library.remedy_core_process_spawn_piped_authorized(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            ctypes.byref(pid),
            ctypes.byref(handle),
            ctypes.byref(stdin_h),
            ctypes.byref(stdout_h),
            ctypes.byref(stderr_h),
        ),
    )
    return PipedSpawnResult(
        pid=int(pid.value),
        handle=int(handle.value),
        stdin_write=int(stdin_h.value),
        stdout_read=int(stdout_h.value),
        stderr_read=int(stderr_h.value),
    )


class ExecCaptureResult(NamedTuple):
    """Outcome of :func:`process_exec_capture_authorized`."""

    exit_code: int
    timed_out: bool
    stdout: bytes
    stderr: bytes


def process_exec_capture_authorized(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    token: bytes,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    owner_confirmed: bool = False,
    now_ms: int | None = None,
    timeout_ms: int = 60_000,
    write_roots: Sequence[str] | None = None,
) -> ExecCaptureResult:
    """Authorized one-shot hidden spawn with stdout/stderr capture.

    *argv[0]* must be absolute. ``timeout_ms`` 0 defaults to 60000 inside Zig.
    On timeout: ``timed_out`` is True and ``exit_code`` is 1. No unsigned soft
    fallback. *write_roots* semantics match :func:`process_spawn_authorized`.
    """
    if write_roots is not None:
        write_jail_set_roots(write_roots)
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = (
        _utf8(json.dumps({str(k): str(v) for k, v in env.items()}))
        if env is not None
        else b""
    )
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    token_raw = bytes(token)
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    exit_code, timed_out = c_uint32(), c_uint8()
    out_so, out_so_len = _BytePtr(), c_size_t()
    out_se, out_se_len = _BytePtr(), c_size_t()
    _check(
        library,
        "process_exec_capture_authorized",
        library.remedy_core_process_exec_capture_authorized(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            int(timeout_ms) & 0xFFFFFFFF,
            ctypes.byref(exit_code),
            ctypes.byref(timed_out),
            ctypes.byref(out_so),
            ctypes.byref(out_so_len),
            ctypes.byref(out_se),
            ctypes.byref(out_se_len),
        ),
    )
    return ExecCaptureResult(
        exit_code=int(exit_code.value),
        timed_out=bool(timed_out.value),
        stdout=_take(library, out_so, out_so_len),
        stderr=_take(library, out_se, out_se_len),
    )


def conpty_spawn_authorized(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    cols: int = 120,
    rows: int = 40,
    token: bytes,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    owner_confirmed: bool = False,
    now_ms: int | None = None,
    write_roots: Sequence[str] | None = None,
) -> tuple[int, int]:
    """Authorized ConPTY spawn. *argv[0]* must be absolute. No unsigned fallback.

    *write_roots* semantics match :func:`process_spawn_authorized`.
    """
    if write_roots is not None:
        write_jail_set_roots(write_roots)
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = (
        _utf8(json.dumps({str(k): str(v) for k, v in env.items()}))
        if env is not None
        else b""
    )
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    token_raw = bytes(token)
    pid, handle = c_uint32(), c_uint64()
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    _check(
        library,
        "conpty_spawn_authorized",
        library.remedy_core_conpty_spawn_authorized(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            int(cols) & 0xFFFF,
            int(rows) & 0xFFFF,
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            ctypes.byref(pid),
            ctypes.byref(handle),
        ),
    )
    return int(pid.value), int(handle.value)
