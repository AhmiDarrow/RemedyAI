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


def test_phase6_soft_spawn_sites_drop_raw_subprocess() -> None:
    """Process list/kill soft sites must not call subprocess.run/pkill/ps."""
    from remedy.core import local_discover
    from remedy.interfaces import uninstaller
    from remedy.tools import comfyui

    for mod in (local_discover, comfyui, uninstaller):
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "subprocess.run" not in source, mod.__name__
        assert "subprocess.call" not in source, mod.__name__
        assert '["ps", "aux"]' not in source, mod.__name__
        assert '["pkill"' not in source, mod.__name__
    stop_src = inspect.getsource(uninstaller._stop_llama_server_processes)
    assert "kill_tree" in stop_src
    assert "matching_processes" in stop_src
    assert "taskkill" not in stop_src


def test_phase1_long_lived_hosts_use_spawn_hidden_not_popen() -> None:
    """claimidx / openserp / mdl / vision / rmb start via Zig authorized spawn."""
    from remedy.runtime import claimidx_host, mdl_runtime, web_search_host
    from remedy.runtime.rmb import service as rmb_service
    from remedy.vision import runtime as vision_runtime

    for mod, needle in (
        (claimidx_host, "def start"),
        (web_search_host, "def start"),
        (mdl_runtime, "def start_tier"),
        (vision_runtime, "def start_server"),
        (rmb_service, "def _spawn"),
    ):
        source = inspect.getsource(mod)
        assert "hidden_subprocess_kwargs" not in source, mod.__name__
        assert "spawn_hidden" in source, mod.__name__
        # Nested _spawn in rmb is defined inside start; still must not Popen.
        if needle == "def _spawn":
            assert "subprocess.Popen" not in source, mod.__name__


def test_voice_bridge_uses_spawn_piped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Voice worker starts through Zig authorized 3-pipe spawn."""
    from remedy.execution.process import PipedProcess
    from remedy.voice import bridge as bridge_mod
    from remedy.voice.bridge import VoiceBridge

    class _FakePipe:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def write(self, _data: str) -> None:
            return None

        def flush(self) -> None:
            return None

        def readline(self) -> str:
            return ""

        def __iter__(self):
            return iter(())

    seen: list[list[str]] = []

    def fake_spawn(argv, **kwargs):
        seen.append(list(argv))
        assert kwargs.get("text") is True
        return PipedProcess(4242, 1, _FakePipe(), _FakePipe(), _FakePipe())

    monkeypatch.setattr(P, "spawn_piped", fake_spawn)
    monkeypatch.setattr(
        bridge_mod.rt,
        "python_path",
        lambda *_a, **_k: tmp_path / "python.exe",
    )
    monkeypatch.setattr(
        bridge_mod.rt,
        "child_env",
        lambda *_a, **_k: {"PATH": "x"},
    )
    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    vb = VoiceBridge(home_dir=tmp_path)
    proc = vb._spawn()
    assert isinstance(proc, PipedProcess)
    assert proc.pid == 4242
    assert seen and "remedy.voice.worker" in seen[0]


def test_voice_stream_pip_uses_run_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    from remedy.voice import service as voice_service

    seen: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = "Successfully installed x\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        assert kwargs.get("capture_output") is True
        return _Done()

    monkeypatch.setattr(P, "run_hidden", fake_run)
    states: list[tuple] = []

    def set_state(pct=None, message=None):
        states.append((pct, message))

    rc, tail = voice_service._stream_pip(
        [sys.executable, "-m", "pip", "install", "x"],
        env={},
        set_state=set_state,
        lo=10.0,
        cap=40.0,
        label="voice pack",
    )
    assert rc == 0
    assert seen and "pip" in seen[0]
    assert any("Successfully" in line for line in tail)


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


def test_run_hidden_capture_uses_exec_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    class _Captured:
        exit_code = 0
        timed_out = False
        stdout = b"cap-ok\n"
        stderr = b""

    def fake_require() -> None:
        return None

    def fake_resolve(argv):
        return [str(a) for a in argv]

    def fake_issue(argv, **_k):
        return b"\x11" * H.CAPABILITY_TOKEN_SIZE, 1

    def fake_capture(argv, cwd=None, env=None, **kwargs):
        _ = (cwd, env, kwargs)
        seen.append(list(argv))
        return _Captured()

    monkeypatch.setattr(P, "require_process_host", fake_require)
    monkeypatch.setattr(P, "resolve_argv0", fake_resolve)
    monkeypatch.setattr(H, "issue_process_spawn_token", fake_issue)
    monkeypatch.setattr(H, "process_exec_capture_authorized", fake_capture)

    result = P.run_hidden(
        [sys.executable, "-c", "print('cap-ok')"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "cap-ok" in (result.stdout or "")
    assert seen and seen[0][0] == sys.executable


def test_piped_soft_helpers_fail_closed_without_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform not in ("win32", "linux"):
        pytest.skip("process host gate is win32/linux")
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED

    monkeypatch.setattr(P, "require_process_host", lambda: None)
    with pytest.raises(HostError) as raised:
        P.popen_hidden([sys.executable, "-c", "pass"])
    assert raised.value.status == STATUS_UNSUPPORTED


def test_popen_hidden_with_pipes_uses_spawn_piped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from remedy.execution.process import PipedProcess

    class _FakePipe:
        closed = False

        def close(self) -> None:
            self.closed = True

    seen: list[list[str]] = []

    def fake_spawn(argv, **kwargs):
        seen.append(list(argv))
        assert kwargs.get("text") is True
        return PipedProcess(7, 1, _FakePipe(), _FakePipe(), _FakePipe())

    monkeypatch.setattr(P, "require_process_host", lambda: None)
    monkeypatch.setattr(P, "spawn_piped", fake_spawn)
    proc = P.popen_hidden(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert isinstance(proc, PipedProcess)
    assert seen and seen[0][0] == sys.executable


@pytest.mark.asyncio
async def test_create_hidden_fail_closed_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform not in ("win32", "linux"):
        pytest.skip("process host gate is win32/linux")
    import asyncio

    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED

    monkeypatch.setattr(P, "require_process_host", lambda: None)
    with pytest.raises(HostError) as raised:
        await P.create_hidden_subprocess_exec(
            sys.executable,
            "-c",
            "pass",
            stdout=asyncio.subprocess.PIPE,
        )
    assert raised.value.status == STATUS_UNSUPPORTED


@pytest.mark.asyncio
async def test_host_session_live_open_fail_closed_off_windows() -> None:
    """Non-Windows must not soft-pipe a HostSession (Zig live open is Win-only)."""
    if sys.platform == "win32":
        pytest.skip("Windows uses Zig HostSession open")
    from remedy.core.computer import host_binding as sess_mod
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED

    session = sess_mod.HostSession(host="posix")
    with pytest.raises(HostError) as raised:
        await session.start()
    assert raised.value.status == STATUS_UNSUPPORTED


@requires_core
@windows_with_core
@pytest.mark.asyncio
async def test_host_session_production_open_is_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remedy.core.computer import host_binding as sess_mod

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
