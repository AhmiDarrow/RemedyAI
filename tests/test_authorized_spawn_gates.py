"""Wave 4 slice: product spawn sites fail closed through Zig authorized spawn."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

from remedy.core.computer import host_binding as H
from remedy.core.computer.host_binding import HostError
from remedy.execution import process as P
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

windows_with_core = pytest.mark.skipif(
    sys.platform != "win32",
    reason="HostSession authorized open is Windows-only",
)


def _core_available() -> bool:
    try:
        H._lib()
        return True
    except (NativeRuntimeUnavailableError, OSError, AttributeError):
        return False


requires_core = pytest.mark.skipif(not _core_available(), reason="remedy_core not built")


def test_spawn_background_source_has_no_raw_popen() -> None:
    from remedy.core.workspace_tools import shell as shell_mod

    source = inspect.getsource(shell_mod._spawn_background)
    assert "Popen(" not in source
    assert "import subprocess" not in source
    assert "spawn_hidden" in source


def test_spawn_background_uses_authorized_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from remedy.core.workspace_tools import shell as shell_mod

    calls: list[tuple] = []

    class _Child:
        pid = 4242

    def fake_spawn(argv, *, cwd=None, env=None, write_roots=None):
        calls.append((list(argv), cwd, env, write_roots))
        return _Child()

    # write-jail helpers may hit a live core; stub the gate used before spawn.
    monkeypatch.setattr(H, "write_jail_set_roots", lambda roots: None)
    monkeypatch.setattr(H, "write_jail_check_spawn", lambda argv, cwd=None: None)
    # `_spawn_background` imports spawn_hidden from process at call time.
    monkeypatch.setattr(P, "spawn_hidden", fake_spawn)

    msg = shell_mod._spawn_background(
        [sys.executable, "-c", "pass"],
        cwd=tmp_path,
        env={"PATH": "x"},
        command="python -c pass",
        write_roots=[tmp_path],
    )
    assert "pid=4242" in msg
    assert calls, "authorized spawn_hidden was not used"
    assert calls[0][0][0] == sys.executable


def test_spawn_background_fail_closed_when_core_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from remedy.core.workspace_tools import shell as shell_mod

    def _deny(*_a, **_k):
        raise NativeRuntimeUnavailableError("remedy_core library not found: test")

    monkeypatch.setattr(H, "write_jail_set_roots", _deny)
    out = shell_mod._spawn_background(
        [sys.executable, "-c", "pass"],
        cwd=tmp_path,
        env=None,
        command="python -c pass",
    )
    assert "SPAWN_FAILED" in out or "authorized spawn unavailable" in out.lower()
    assert "pid=" not in out


def test_run_hidden_no_pipes_uses_spawn_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[list[str]] = []

    class _Child:
        def wait(self, timeout=None):
            return 0

        def close(self):
            return None

        def kill_tree(self):
            return None

    def fake_require() -> None:
        return None

    def fake_spawn(argv, *, cwd=None, env=None, write_roots=None):
        _ = (cwd, env, write_roots)
        seen.append(list(argv))
        return _Child()

    monkeypatch.setattr(P, "require_process_host", fake_require)
    monkeypatch.setattr(P, "spawn_hidden", fake_spawn)
    monkeypatch.setattr(P, "wait", lambda child, timeout=None: 0)

    result = P.run_hidden([sys.executable, "-c", "pass"], timeout=5)
    assert result.returncode == 0
    assert seen and seen[0][0] == sys.executable


def test_run_hidden_fail_closed_family(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform not in ("win32", "linux"):
        pytest.skip("process host gate is win32/linux")

    def _boom() -> None:
        raise NativeRuntimeUnavailableError("missing core")

    monkeypatch.setattr(P, "require_process_host", _boom)
    with pytest.raises((NativeRuntimeUnavailableError, HostError, OSError)):
        P.run_hidden([sys.executable, "-c", "pass"], timeout=1)


@requires_core
@windows_with_core
@pytest.mark.asyncio
async def test_host_session_production_open_is_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remedy.execution.host import session as sess_mod

    opens: list[str] = []

    real_authorized = H.host_session_open_authorized

    def track_authorized(*args, **kwargs):
        opens.append("authorized")
        return real_authorized(*args, **kwargs)

    def boom_unsigned(*_a, **_k):
        opens.append("unsigned")
        raise AssertionError("production open must not use unsigned host_session_open")

    monkeypatch.setattr(H, "host_session_open_authorized", track_authorized)
    monkeypatch.setattr(H, "host_session_open", boom_unsigned)

    session = sess_mod.HostSession(host="cmd", use_conpty=False)
    try:
        await session.start()
        assert session._zig_handle
        assert opens == ["authorized"]
        res = await session.run("echo auth-open-ok", timeout=20.0)
        assert res.timed_out is False
        assert "auth-open-ok" in res.stdout
    finally:
        await session.close()


@requires_core
@windows_with_core
def test_host_session_open_authorized_rejects_bad_token() -> None:
    with pytest.raises(HostError) as raised:
        H.host_session_open_authorized(
            host="cmd",
            token=b"\x00" * H.CAPABILITY_TOKEN_SIZE,
            now_ms=0,
        )
    assert raised.value.status in (
        H.STATUS_ACCESS_DENIED,
        H.STATUS_INVALID_ARGUMENT,
        H.STATUS_OPERATION_FAILED,
    )
