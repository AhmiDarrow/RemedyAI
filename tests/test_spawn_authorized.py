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


@pytest.fixture(autouse=True)
def _clear_write_jail_between_tests():
    """Sandbox/hop tests may leave Zig write-jail roots set process-wide."""
    with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError):
        H.write_jail_clear()
    yield
    with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError):
        H.write_jail_clear()


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
def test_policy_hash_spawn_covers_env(test_signing_key):
    _ = test_signing_key
    argv = [r"C:\Windows\System32\cmd.exe", "/c", "echo"]
    argv_only = H.policy_hash_spawn(argv, None, False)
    with_env = H.policy_hash_spawn(argv, {"FOO": "bar"}, False)
    again = H.policy_hash_spawn(argv, {"FOO": "bar"}, False)
    replaced = H.policy_hash_spawn(argv, {"FOO": "bar"}, True)
    assert argv_only != with_env
    assert with_env == again
    assert replaced != with_env
    assert len(argv_only) == 32


@windows_with_core
def test_authorized_spawn_runs_cmd_with_token(test_signing_key, tmp_path: Path):
    _ = test_signing_key
    argv = P.resolve_argv0(["cmd", "/c", "exit %REMEDY_AUTH_CODE%"])
    env = {"REMEDY_AUTH_CODE": "5", "SystemRoot": r"C:\Windows"}
    token, now = H.issue_process_spawn_token(argv, env=env)
    pid, handle = H.process_spawn_authorized(
        argv,
        cwd=str(tmp_path),
        env=env,
        token=token,
        now_ms=now,
    )
    try:
        assert pid > 0 and handle
        code = H.process_wait(handle, 10_000)
        assert code == 5
    finally:
        H.process_close(handle)


def test_run_hidden_exec_capture_threads_replace_env(monkeypatch):
    """Mint and spend must share replace_env on the exec-capture path."""
    seen: dict[str, bool] = {}

    def issue(argv, env=None, replace_env=False, **kwargs):
        _ = (argv, env, kwargs)
        seen["issue"] = bool(replace_env)
        return b"tok", 1

    class _Cap:
        timed_out = False
        exit_code = 0
        stdout = b""
        stderr = b""

    def capture(*args, replace_env=False, **kwargs):
        _ = (args, kwargs)
        seen["spend"] = bool(replace_env)
        return _Cap()

    monkeypatch.setattr(H, "issue_process_spawn_token", issue)
    monkeypatch.setattr(H, "process_exec_capture_authorized", capture)
    monkeypatch.setattr(P, "require_process_host", lambda: None)
    P.run_hidden(
        [sys.executable, "-c", "pass"],
        capture_output=True,
        replace_env=True,
        timeout=1,
    )
    assert seen.get("issue") is True
    assert seen.get("spend") is True


@windows_with_core
def test_replace_env_token_cannot_spend_as_merge(test_signing_key, tmp_path: Path):
    """A token minted for replace_env=true must not spend on a merge payload."""
    _ = test_signing_key
    argv = P.resolve_argv0(["cmd", "/c", "echo"])
    env = {"FOO": "bar", "SystemRoot": r"C:\Windows"}
    token, now = H.issue_process_spawn_token(argv, env=env, replace_env=True)
    with pytest.raises(H.HostError) as raised:
        H.process_spawn_authorized(
            argv,
            cwd=str(tmp_path),
            env=env,
            token=token,
            now_ms=now,
            replace_env=False,
        )
    assert raised.value.status == H.STATUS_ACCESS_DENIED


def test_host_session_start_mints_the_env_it_spends() -> None:
    """HostSession.start always passes a scrubbed env; the token must cover it."""
    import inspect

    from remedy.core.computer.host_binding import _session

    src = inspect.getsource(_session.HostSession.start)
    assert "issue_host_session_token(self.host, env=env)" in src
    src_issue = inspect.getsource(_session.issue_host_session_token)
    assert "env=env" in src_issue
    assert "replace_env=replace_env" in src_issue


@windows_with_core
def test_host_session_env_token_cannot_spend_as_argv_only(
    test_signing_key, tmp_path: Path
) -> None:
    """A session open with env must not spend an argv-only token."""
    _ = (test_signing_key, tmp_path)
    env = {"REMEDY_AUTH_CODE": "1", "SystemRoot": r"C:\Windows"}
    token, now = H.issue_host_session_token("cmd")
    with pytest.raises(H.HostError) as raised:
        H.host_session_open_authorized(
            host="cmd",
            env=env,
            use_conpty=False,
            token=token,
            now_ms=now,
        )
    assert raised.value.status == H.STATUS_ACCESS_DENIED
    token, now = H.issue_host_session_token("cmd", env=env)
    handle = H.host_session_open_authorized(
        host="cmd",
        env=env,
        use_conpty=False,
        token=token,
        now_ms=now,
    )
    try:
        assert handle > 0
    finally:
        H.host_session_close(handle)


@windows_with_core
def test_argv_only_token_cannot_spend_on_env(test_signing_key, tmp_path: Path):
    """PolicyEnvStrict: a token minted for inherit cannot authorize a spawn env."""
    _ = test_signing_key
    argv = P.resolve_argv0(["cmd", "/c", "echo"])
    token, now = H.issue_process_spawn_token(argv)
    with pytest.raises(H.HostError) as raised:
        H.process_spawn_authorized(
            argv,
            cwd=str(tmp_path),
            env={"FOO": "bar", "SystemRoot": r"C:\Windows"},
            token=token,
            now_ms=now,
        )
    assert raised.value.status == H.STATUS_ACCESS_DENIED


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
    argv = P.resolve_argv0(["cmd", "/c", "exit 0"])
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
    argv = P.resolve_argv0(["cmd", "/c", "exit 0"])
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
    argv = P.resolve_argv0(["cmd", "/c", "exit 0"])
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
    argv = P.resolve_argv0(["cmd", "/c", "exit %REMEDY_AUTH_CODE%"])
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
    argv = P.resolve_argv0(["cmd", "/c", "type", str(auth)])
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
    argv = P.resolve_argv0(["cmd", "/c", "copy", "a.txt", str(dest)])
    H.write_jail_set_roots([str(project)])
    with pytest.raises(H.HostError) as raised:
        H.write_jail_check_spawn(argv, cwd=str(project))
    assert raised.value.status == H.STATUS_ACCESS_DENIED
    # Relative dest under jailed cwd is fine
    ok = P.resolve_argv0(["cmd", "/c", "copy", "a.txt", "out.txt"])
    H.write_jail_check_spawn(ok, cwd=str(project))


@windows_with_core
def test_write_jail_empty_roots_is_full(test_signing_key, tmp_path: Path, clear_write_jail):
    _ = (test_signing_key, clear_write_jail)
    anywhere = tmp_path / "anywhere"
    anywhere.mkdir()
    argv = P.resolve_argv0(["cmd", "/c", "exit 0"])
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


@windows_with_core
def test_authorized_spawn_write_roots_do_not_stick(
    test_signing_key, tmp_path: Path, clear_write_jail
):
    """write_roots on spawn is for that jail check only — must clear afterwards."""
    _ = (test_signing_key, clear_write_jail)
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    argv = P.resolve_argv0(["cmd", "/c", "exit 0"])
    token, now = H.issue_process_spawn_token(argv)
    pid, handle = H.process_spawn_authorized(
        argv,
        cwd=str(project),
        token=token,
        now_ms=now,
        write_roots=[str(project)],
    )
    try:
        assert H.process_wait(handle, 10_000) == 0
    finally:
        H.process_close(handle)
    # Sticky project roots would deny this path; Full/cleared must allow it.
    H.write_jail_check_path(str(outside / "x.txt"), cwd=str(outside))
