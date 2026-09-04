"""Authorized spawn: policy + capability token on the Zig ABI 5 surface."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import pytest

from remedy.core.computer import host_binding as H
from remedy.execution import process as P
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

windows_with_core = pytest.mark.skipif(
    sys.platform != "win32",
    reason="authorized spawn is implemented on the Windows host",
)


@pytest.fixture
def test_signing_key(monkeypatch):
    key = bytes(range(32))
    monkeypatch.setenv("REMEDY_SPAWN_SIGNING_KEY", key.hex())
    with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError):
        H.security_clear_signing_key()
    H.security_set_signing_key(key)
    yield key
    with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError):
        H.security_clear_signing_key()


@windows_with_core
def test_authorized_spawn_runs_cmd_with_token(test_signing_key, tmp_path: Path):
    _ = test_signing_key
    argv = P._resolve_argv0(["cmd", "/c", "exit %REMEDY_AUTH_CODE%"])
    token, now = H.issue_process_spawn_token(argv)
    pid, handle = H.process_spawn_authorized(
        argv,
        cwd=str(tmp_path),
        env={"REMEDY_AUTH_CODE": "5", "SystemRoot": r"C:\Windows"},
        token=token,
        now_ms=now,
    )
    try:
        assert pid > 0 and handle
        code = H.process_wait(handle, 10_000)
        assert code == 5
    finally:
        H.process_close(handle)


@windows_with_core
def test_spawn_hidden_uses_authorized_path(test_signing_key, tmp_path: Path):
    _ = test_signing_key
    with P.spawn_hidden(
        ["cmd", "/c", "exit %REMEDY_AUTH_CODE%"],
        cwd=tmp_path,
        env={"REMEDY_AUTH_CODE": "9", "SystemRoot": r"C:\Windows"},
    ) as child:
        assert child.wait(10.0) == 9


@windows_with_core
def test_dangerous_basename_is_denied(test_signing_key):
    _ = test_signing_key
    # Absolute path required by validateArguments; basename still matches denylist.
    sudo = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "shutdown.exe"
    argv = [str(sudo), "/?"]
    token, now = H.issue_process_spawn_token(argv)
    with pytest.raises(H.HostError) as raised:
        H.process_spawn_authorized(argv, token=token, now_ms=now)
    assert raised.value.status == H.STATUS_ACCESS_DENIED


@windows_with_core
def test_missing_token_is_denied(test_signing_key):
    _ = test_signing_key
    argv = P._resolve_argv0(["cmd", "/c", "exit 0"])
    with pytest.raises(H.HostError) as raised:
        H.process_spawn_authorized(argv, token=b"\x00" * H.CAPABILITY_TOKEN_SIZE, now_ms=0)
    assert raised.value.status == H.STATUS_ACCESS_DENIED


@windows_with_core
def test_spawn_hidden_never_calls_unsigned_export(test_signing_key, tmp_path: Path, monkeypatch):
    """Production spawn_hidden must not soft-fallback to unsigned process_spawn_hidden."""
    _ = test_signing_key

    def _boom(*_a, **_k):
        raise AssertionError("unsigned process_spawn_hidden must not be used")

    monkeypatch.setattr(H, "process_spawn_hidden", _boom)
    with P.spawn_hidden(
        ["cmd", "/c", "exit %REMEDY_AUTH_CODE%"],
        cwd=tmp_path,
        env={"REMEDY_AUTH_CODE": "3", "SystemRoot": r"C:\Windows"},
    ) as child:
        assert child.wait(10.0) == 3


@windows_with_core
def test_clear_signing_key_denies_until_reinstalled(tmp_path: Path):
    """After clear, authorized spawn fails hard (no unsigned fallback)."""
    key = bytes(range(32))
    H.security_set_signing_key(key)
    H.security_clear_signing_key()
    argv = P._resolve_argv0(["cmd", "/c", "exit 0"])
    # issue_process_spawn_token will mint an ephemeral key via ensure_*; clear again
    # so the Zig verifier is empty while Python still holds a stale token shape.
    token, now = H.issue_process_spawn_token(argv)
    H.security_clear_signing_key()
    with pytest.raises(H.HostError) as raised:
        H.process_spawn_authorized(
            argv,
            cwd=str(tmp_path),
            token=token,
            now_ms=now,
        )
    assert raised.value.status == H.STATUS_ACCESS_DENIED
