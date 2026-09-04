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


@pytest.fixture
def clear_write_jail():
    with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError):
        H.write_jail_clear()
    yield
    with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError):
        H.write_jail_clear()


@windows_with_core
def test_write_jail_denies_cwd_outside_roots(test_signing_key, tmp_path: Path, clear_write_jail):
    _ = (test_signing_key, clear_write_jail)
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    argv = P._resolve_argv0(["cmd", "/c", "exit 0"])
    token, now = H.issue_process_spawn_token(argv)
    with pytest.raises(H.HostError) as raised:
        H.process_spawn_authorized(
            argv,
            cwd=str(outside),
            token=token,
            now_ms=now,
            write_roots=[str(project)],
        )
    assert raised.value.status == H.STATUS_ACCESS_DENIED


@windows_with_core
def test_write_jail_allows_project_cwd(test_signing_key, tmp_path: Path, clear_write_jail):
    _ = clear_write_jail
    _ = test_signing_key
    project = tmp_path / "proj"
    project.mkdir()
    argv = P._resolve_argv0(["cmd", "/c", "exit %REMEDY_AUTH_CODE%"])
    with P.spawn_hidden(
        argv,
        cwd=project,
        env={"REMEDY_AUTH_CODE": "7", "SystemRoot": r"C:\Windows"},
        write_roots=[str(project)],
    ) as child:
        assert child.wait(10.0) == 7


@windows_with_core
def test_write_jail_auth_path_always_denied(test_signing_key, tmp_path: Path, clear_write_jail):
    _ = (test_signing_key, clear_write_jail)
    H.write_jail_clear()  # Full — workdir unbound, auth still closed
    auth = tmp_path / ".remedy" / "auth" / "local_api_token"
    auth.parent.mkdir(parents=True)
    auth.write_text("x", encoding="utf-8")
    argv = P._resolve_argv0(["cmd", "/c", "type", str(auth)])
    with pytest.raises(H.HostError) as raised:
        H.write_jail_check_spawn(argv, cwd=str(tmp_path))
    assert raised.value.status == H.STATUS_ACCESS_DENIED


@windows_with_core
def test_write_jail_blocks_absolute_mutation_dest(test_signing_key, tmp_path: Path, clear_write_jail):
    _ = (test_signing_key, clear_write_jail)
    project = tmp_path / "proj"
    project.mkdir()
    dest = tmp_path / "outside" / "pwn.txt"
    dest.parent.mkdir()
    argv = P._resolve_argv0(["cmd", "/c", "copy", "a.txt", str(dest)])
    H.write_jail_set_roots([str(project)])
    with pytest.raises(H.HostError) as raised:
        H.write_jail_check_spawn(argv, cwd=str(project))
    assert raised.value.status == H.STATUS_ACCESS_DENIED
    # Relative dest under jailed cwd is fine
    ok = P._resolve_argv0(["cmd", "/c", "copy", "a.txt", "out.txt"])
    H.write_jail_check_spawn(ok, cwd=str(project))


@windows_with_core
def test_write_jail_empty_roots_is_full(test_signing_key, tmp_path: Path, clear_write_jail):
    _ = (test_signing_key, clear_write_jail)
    anywhere = tmp_path / "anywhere"
    anywhere.mkdir()
    argv = P._resolve_argv0(["cmd", "/c", "exit 0"])
    token, now = H.issue_process_spawn_token(argv)
    pid, handle = H.process_spawn_authorized(
        argv,
        cwd=str(anywhere),
        token=token,
        now_ms=now,
        write_roots=[],
    )
    try:
        assert H.process_wait(handle, 10_000) == 0
    finally:
        H.process_close(handle)
