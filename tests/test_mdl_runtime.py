"""The MDL tier runtime starts, stops and health-checks local llama-servers.

Everything here spawns or kills OS processes and opens sockets, so the failure
modes are expensive rather than cosmetic:

- a health check that trusts any URL is an SSRF hole (the base URL comes from
  config, and the checker runs with the owner's network access);
- "is this tier running?" answering yes for a dead child means the router sends
  inference at a port nobody is listening on;
- stop_tier reporting ``ok`` while the child is still alive leaks a
  multi-hundred-megabyte model in VRAM until reboot;
- the resident LIGHT tier must survive the idle reaper, or Remedy's continuity
  core silently disappears mid-conversation.

These tests never spawn a real process, never kill a real PID and never open a
real socket: ``socket.create_connection``, ``subprocess.Popen`` and
``subprocess.run`` are all stubbed out by an autouse fixture so a wrong test
cannot taskkill something on the machine running the suite.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from urllib.request import Request

import pytest

from remedy.runtime import mdl_runtime as mr
from remedy.runtime.mdl import MDL_TIERS


class _Stop(BaseException):
    """Breaks out of the watcher's ``while True`` — deliberately not an
    ``Exception``, which the watcher swallows by design."""


class FakeProc:
    """Stand-in for ``subprocess.Popen`` with a scriptable lifecycle."""

    def __init__(
        self,
        *,
        alive: bool = True,
        returncode: int = 0,
        pid: int = 4242424,
        terminate_error: BaseException | None = None,
        wait_timeouts: int = 0,
        kill_works: bool = True,
    ) -> None:
        self._alive = alive
        self.returncode = returncode
        self.pid = pid
        self._terminate_error = terminate_error
        self._wait_timeouts = wait_timeouts
        self._kill_works = kill_works
        self.terminated = False
        self.killed = False
        self.waits: list[float | None] = []

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        if self._terminate_error is not None:
            raise self._terminate_error

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self._wait_timeouts > 0:
            self._wait_timeouts -= 1
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        self._alive = False
        return self.returncode

    def kill(self):
        self.killed = True
        if self._kill_works:
            self._alive = False


class FakeResp:
    def __init__(self, status: int | None = 200) -> None:
        if status is not None:
            self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Reset module globals and cut every path to the real machine."""
    monkeypatch.setattr(mr, "_tier_procs", {"light": None, "medium": None, "full": None})
    monkeypatch.setattr(mr, "_tier_last_used", {"light": 0.0, "medium": 0.0, "full": 0.0})
    monkeypatch.setattr(mr, "_tier_running_watcher", False)

    def _no_socket(*a, **k):
        raise OSError("socket blocked in tests")

    def _no_popen(*a, **k):
        raise AssertionError("test spawned a real process")

    def _no_run(*a, **k):
        raise AssertionError("test ran a real command")

    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.setattr(subprocess, "run", _no_run)


def _binary(tmp_path, name="llama-server.exe"):
    p = tmp_path / name
    p.write_text("#!/bin/false\n", encoding="utf-8")
    return p


def _model(tmp_path, name="model.gguf"):
    p = tmp_path / name
    p.write_bytes(b"GGUF")
    return p


# --------------------------------------------------------------------------
# _port_open
# --------------------------------------------------------------------------


def test_a_refused_port_is_reported_not_raised(monkeypatch):
    assert mr._port_open("127.0.0.1", 8741) is False


def test_an_open_port_is_reported_and_the_socket_is_closed(monkeypatch):
    closed = []

    class Sock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: Sock())
    assert mr._port_open("127.0.0.1", 8741) is True
    assert closed == [True]


def test_the_port_probe_uses_a_short_timeout_and_the_given_address(monkeypatch):
    seen = {}

    def _capture(addr, timeout=None):
        seen["addr"] = addr
        seen["timeout"] = timeout
        raise OSError("refused")

    monkeypatch.setattr(socket, "create_connection", _capture)
    mr._port_open("127.0.0.1", 8742)
    assert seen["addr"] == ("127.0.0.1", 8742)
    assert seen["timeout"] == 0.15


def test_a_non_oserror_from_the_socket_layer_is_not_swallowed(monkeypatch):
    """Only connection failures mean "closed"; a programming error must surface."""

    def _boom(*a, **k):
        raise ValueError("bad address tuple")

    monkeypatch.setattr(socket, "create_connection", _boom)
    with pytest.raises(ValueError):
        mr._port_open("127.0.0.1", 8741)


# --------------------------------------------------------------------------
# _health
# --------------------------------------------------------------------------


def _patch_urlopen(monkeypatch, handler):
    from remedy.core import security

    monkeypatch.setattr(security, "urlopen_no_redirect", handler)


@pytest.mark.parametrize("base", ["", None, "   ", "/"])
def test_an_empty_base_url_is_never_probed(monkeypatch, base):
    _patch_urlopen(monkeypatch, lambda *a, **k: pytest.fail("should not be called"))
    assert mr._health(base) is False


@pytest.mark.parametrize(
    "base",
    [
        "http://169.254.169.254/v1",  # cloud metadata
        "http://10.0.0.5:8740/v1",  # LAN
        "file:///etc/passwd",
        "ftp://127.0.0.1/v1",
        "http://user:pw@127.0.0.1:8740/v1",  # userinfo smuggling
    ],
)
def test_a_non_loopback_base_url_is_refused_before_any_request(monkeypatch, base):
    _patch_urlopen(monkeypatch, lambda *a, **k: pytest.fail("SSRF: request was sent"))
    assert mr._health(base) is False


def test_a_two_hundred_response_means_healthy(monkeypatch):
    _patch_urlopen(monkeypatch, lambda req, timeout=None: FakeResp(200))
    assert mr._health("http://127.0.0.1:8740/v1") is True


@pytest.mark.parametrize("status", [301, 404, 500, 503])
def test_a_non_success_status_is_not_healthy(monkeypatch, status):
    _patch_urlopen(monkeypatch, lambda req, timeout=None: FakeResp(status))
    assert mr._health("http://127.0.0.1:8740/v1") is False


def test_a_response_without_a_status_attribute_is_assumed_ok(monkeypatch):
    _patch_urlopen(monkeypatch, lambda req, timeout=None: FakeResp(status=None))
    assert mr._health("http://127.0.0.1:8740/v1") is True


def test_a_connection_error_on_both_attempts_is_reported_not_raised(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("connection refused")

    _patch_urlopen(monkeypatch, _boom)
    assert mr._health("http://127.0.0.1:8740/v1") is False


def test_the_probe_hits_models_under_the_given_base(monkeypatch):
    seen = []

    def _capture(req, timeout=None):
        seen.append((req.full_url, req.get_header("User-agent"), timeout))
        return FakeResp(200)

    _patch_urlopen(monkeypatch, _capture)
    assert mr._health("http://127.0.0.1:8740/v1/", timeout=0.25) is True
    assert seen == [("http://127.0.0.1:8740/v1/models", "RemedyAI-mdl/1.0", 0.25)]


def test_the_fallback_probe_for_a_v1_base_repeats_the_same_url(monkeypatch):
    """Documents current behaviour: for a ``/v1`` base — the only shape
    ``get_tier_base_url`` ever produces — the retry URL is byte-identical to
    the first, so the fallback can never find an endpoint the first attempt
    missed. See BUGS in the handoff."""
    seen = []

    def _capture(req, timeout=None):
        seen.append(req.full_url)
        raise OSError("nope")

    _patch_urlopen(monkeypatch, _capture)
    assert mr._health("http://127.0.0.1:8740/v1") is False
    assert seen == ["http://127.0.0.1:8740/v1/models", "http://127.0.0.1:8740/v1/models"]


def test_a_non_v1_base_falls_back_to_the_v1_path(monkeypatch):
    calls = []

    def _capture(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise OSError("404-ish")
        return FakeResp(200)

    _patch_urlopen(monkeypatch, _capture)
    assert mr._health("http://127.0.0.1:8740") is True
    assert calls == ["http://127.0.0.1:8740/models", "http://127.0.0.1:8740/v1/models"]


def test_the_health_request_carries_no_credentials(monkeypatch):
    seen = {}

    def _capture(req: Request, timeout=None):
        seen["headers"] = dict(req.headers)
        return FakeResp(200)

    _patch_urlopen(monkeypatch, _capture)
    mr._health("http://127.0.0.1:8740/v1")
    assert list(seen["headers"]) == ["User-agent"]


# --------------------------------------------------------------------------
# is_tier_running
# --------------------------------------------------------------------------


def test_an_unknown_tier_is_never_running(monkeypatch):
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: pytest.fail("probed unknown tier"))
    assert mr.is_tier_running("gigantic") is False


def test_a_live_child_with_an_open_port_needs_no_http_probe(monkeypatch):
    mr._tier_procs["light"] = FakeProc(alive=True)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: pytest.fail("unnecessary HTTP probe"))
    assert mr.is_tier_running("light") is True


def test_a_live_child_with_a_closed_port_is_not_running(monkeypatch):
    """The process existing is not the same as the server accepting work."""
    mr._tier_procs["light"] = FakeProc(alive=True)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: pytest.fail("port was closed"))
    assert mr.is_tier_running("light") is False


def test_a_dead_child_with_an_open_port_falls_back_to_the_health_probe(monkeypatch):
    mr._tier_procs["medium"] = FakeProc(alive=False, returncode=1)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    probed = []
    monkeypatch.setattr(mr, "_health", lambda base: probed.append(base) or True)
    assert mr.is_tier_running("medium") is True
    assert probed == ["http://127.0.0.1:8742/v1"]


def test_a_foreign_listener_that_fails_the_health_probe_is_not_our_tier(monkeypatch):
    """Something else squatting the port must not be mistaken for llama-server."""
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda base: False)
    assert mr.is_tier_running("full") is False


def test_the_tiers_probe_their_own_ports(monkeypatch):
    seen = []
    monkeypatch.setattr(mr, "_port_open", lambda host, port: seen.append((host, port)) or False)
    for name in MDL_TIERS:
        mr.is_tier_running(name)
    assert seen == [("127.0.0.1", MDL_TIERS[n].port) for n in MDL_TIERS]


# --------------------------------------------------------------------------
# mark_tier_used
# --------------------------------------------------------------------------


def test_marking_a_tier_used_records_now():
    before = time.time()
    mr.mark_tier_used("medium")
    assert mr._tier_last_used["medium"] >= before


def test_marking_an_unknown_tier_is_ignored_and_creates_no_entry():
    mr.mark_tier_used("gigantic")
    assert "gigantic" not in mr._tier_last_used


# --------------------------------------------------------------------------
# tier_idle_stop
# --------------------------------------------------------------------------


def test_an_unknown_tier_is_not_idle_stopped(monkeypatch):
    monkeypatch.setattr(mr, "stop_tier", lambda *a, **k: pytest.fail("stopped unknown tier"))
    assert mr.tier_idle_stop("gigantic") is False


def test_the_resident_tier_is_never_idle_stopped(monkeypatch):
    """LIGHT is the continuity core: idle for an hour still means keep it."""
    monkeypatch.setattr(mr, "stop_tier", lambda *a, **k: pytest.fail("stopped the resident tier"))
    mr._tier_last_used["light"] = 0.0  # epoch — maximally idle
    assert mr.tier_idle_stop("light", idle_s=1.0) is False


def test_a_recently_used_tier_is_left_alone(monkeypatch):
    monkeypatch.setattr(mr, "stop_tier", lambda *a, **k: pytest.fail("stopped a busy tier"))
    mr.mark_tier_used("full")
    assert mr.tier_idle_stop("full", idle_s=600) is False


def test_an_idle_tier_is_stopped():
    mr._tier_last_used["medium"] = time.time() - 10_000
    result = mr.tier_idle_stop("medium", idle_s=600)
    # Annotated ``-> bool`` but stop_tier's dict is returned verbatim.
    assert result == {"ok": True, "stopped": False, "tier": "medium"}


def test_a_tier_never_used_counts_as_idle():
    """last_used 0.0 means "never" — that must reap, not be treated as fresh."""
    assert mr.tier_idle_stop("full", idle_s=600) == {"ok": True, "stopped": False, "tier": "full"}


# --------------------------------------------------------------------------
# stop_tier
# --------------------------------------------------------------------------


def test_stopping_an_unknown_tier_is_an_error_not_a_crash():
    assert mr.stop_tier("gigantic") == {"ok": False, "error": "Unknown tier: gigantic"}


def test_stopping_a_tier_with_no_child_reports_nothing_stopped():
    assert mr.stop_tier("medium") == {"ok": True, "stopped": False, "tier": "medium"}


def test_stopping_a_live_child_terminates_it_and_clears_the_slot(monkeypatch):
    monkeypatch.setattr(mr.os, "name", "posix")
    proc = FakeProc(alive=True)
    mr._tier_procs["medium"] = proc
    result = mr.stop_tier("medium")
    assert proc.terminated and not proc.killed
    assert proc.waits == [5]
    assert mr._tier_procs["medium"] is None
    assert result == {"ok": True, "stopped": True, "tier": "medium"}


def test_a_child_that_ignores_terminate_is_killed(monkeypatch):
    monkeypatch.setattr(mr.os, "name", "posix")
    proc = FakeProc(alive=True, wait_timeouts=1)
    mr._tier_procs["full"] = proc
    result = mr.stop_tier("full")
    assert proc.killed is True
    assert proc.waits == [5, 3]
    assert result["stopped"] is True


def test_a_child_that_survives_kill_is_still_reported_stopped(monkeypatch):
    """Documents current behaviour: after terminate+kill both time out the
    result claims ``stopped`` anyway, so callers cannot detect a zombie."""
    monkeypatch.setattr(mr.os, "name", "posix")
    proc = FakeProc(alive=True, wait_timeouts=2, kill_works=False)
    mr._tier_procs["light"] = proc
    assert mr.stop_tier("light") == {"ok": True, "stopped": True, "tier": "light"}
    assert proc.poll() is None  # still alive


def test_a_terminate_that_raises_is_swallowed_but_the_slot_is_still_cleared(monkeypatch):
    monkeypatch.setattr(mr.os, "name", "posix")
    proc = FakeProc(alive=True, terminate_error=PermissionError("access denied"))
    mr._tier_procs["medium"] = proc
    result = mr.stop_tier("medium")
    assert mr._tier_procs["medium"] is None
    assert result == {"ok": True, "stopped": False, "tier": "medium"}


def test_an_already_exited_child_is_not_terminated_again(monkeypatch):
    monkeypatch.setattr(mr.os, "name", "posix")
    proc = FakeProc(alive=False, returncode=0)
    mr._tier_procs["medium"] = proc
    result = mr.stop_tier("medium")
    assert proc.terminated is False
    # the slot keeps the dead handle: only the live branch clears it
    assert mr._tier_procs["medium"] is proc
    assert result == {"ok": True, "stopped": False, "tier": "medium"}


def test_on_windows_the_process_tree_is_taskkilled(monkeypatch):
    """llama-server spawns children; terminate() alone leaves them holding VRAM."""
    monkeypatch.setattr(mr.os, "name", "nt")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    proc = FakeProc(alive=True, pid=1234)
    mr._tier_procs["full"] = proc
    assert mr.stop_tier("full")["stopped"] is True
    args, kw = calls[0]
    assert args == ["taskkill", "/F", "/PID", "1234", "/T"]
    assert kw["check"] is False and kw["timeout"] == 10


def test_a_failing_taskkill_does_not_break_the_stop(monkeypatch):
    monkeypatch.setattr(mr.os, "name", "nt")

    def _boom(*a, **k):
        raise OSError("taskkill missing")

    monkeypatch.setattr(subprocess, "run", _boom)
    mr._tier_procs["full"] = FakeProc(alive=True)
    assert mr.stop_tier("full")["ok"] is True


def test_no_child_means_no_taskkill(monkeypatch):
    monkeypatch.setattr(mr.os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("taskkilled nothing"))
    assert mr.stop_tier("light")["stopped"] is False


# --------------------------------------------------------------------------
# start_tier
# --------------------------------------------------------------------------


def _popen_recorder(monkeypatch, proc=None):
    recorded = {}

    def _popen(cmd, **kwargs):
        recorded["cmd"] = cmd
        recorded["kwargs"] = kwargs
        return proc if proc is not None else FakeProc(alive=True)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    return recorded


def test_starting_an_unknown_tier_is_an_error(tmp_path):
    out = mr.start_tier(
        "gigantic",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
    )
    assert out == {"ok": False, "error": "Unknown tier: gigantic"}


def test_starting_a_running_tier_is_a_no_op_that_refreshes_the_idle_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda name: True)
    out = mr.start_tier(
        "medium",
        model_path=str(tmp_path / "missing.gguf"),
        runtime_binary=str(tmp_path / "missing.exe"),
    )
    assert out == {
        "ok": True,
        "already_running": True,
        "base_url": "http://127.0.0.1:8742/v1",
        "tier": "medium",
    }
    assert mr._tier_last_used["medium"] > 0


@pytest.mark.parametrize("name", ["nope.exe", ""])
def test_a_missing_runtime_binary_is_reported_not_spawned(monkeypatch, tmp_path, name):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    out = mr.start_tier(
        "medium",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(tmp_path / name),
    )
    assert out["ok"] is False
    assert "binary not found" in out["error"]


def test_a_directory_is_not_a_runtime_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    d = tmp_path / "bindir"
    d.mkdir()
    out = mr.start_tier("medium", model_path=str(_model(tmp_path)), runtime_binary=str(d))
    assert out["ok"] is False and "binary not found" in out["error"]


def test_a_missing_model_file_is_reported_not_spawned(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    out = mr.start_tier(
        "medium",
        model_path=str(tmp_path / "absent.gguf"),
        runtime_binary=str(_binary(tmp_path)),
    )
    assert out["ok"] is False
    assert "Model file not found" in out["error"]


def test_a_spawn_failure_is_returned_and_leaves_no_handle(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)

    def _boom(*a, **k):
        raise OSError("ENOEXEC")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    out = mr.start_tier(
        "medium",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
    )
    assert out["ok"] is False
    assert "Failed to start tier medium" in out["error"]
    assert mr._tier_procs["medium"] is None


def test_the_command_line_carries_the_tier_port_and_layer_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: True)
    binary = _binary(tmp_path)
    model = _model(tmp_path)
    rec = _popen_recorder(monkeypatch)

    out = mr.start_tier("medium", model_path=str(model), runtime_binary=str(binary))

    assert out["ok"] is True
    cmd = rec["cmd"]
    assert cmd[0] == str(binary)
    assert cmd[cmd.index("--port") + 1] == "8742"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("-ngl") + 1] == "16"  # medium tier's n_layers
    assert cmd[cmd.index("-m") + 1] == str(model)
    assert "--mmproj" not in cmd
    assert rec["kwargs"]["cwd"] == str(binary.parent)
    assert rec["kwargs"]["stdout"] == subprocess.DEVNULL


@pytest.mark.parametrize("ngl", [0, 1, 24])
def test_an_explicit_gpu_layer_count_overrides_the_tier_default(monkeypatch, tmp_path, ngl):
    """0 is a real value (CPU-only) and must not be mistaken for "unset"."""
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: True)
    rec = _popen_recorder(monkeypatch)
    mr.start_tier(
        "light",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
        n_gpu_layers=ngl,
    )
    cmd = rec["cmd"]
    assert cmd[cmd.index("-ngl") + 1] == str(ngl)


def test_an_existing_mmproj_is_passed_through(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: True)
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"MM")
    rec = _popen_recorder(monkeypatch)
    mr.start_tier(
        "full",
        model_path=str(_model(tmp_path)),
        mmproj_path=str(mmproj),
        runtime_binary=str(_binary(tmp_path)),
    )
    cmd = rec["cmd"]
    assert cmd[cmd.index("--mmproj") + 1] == str(mmproj)


def test_a_missing_mmproj_is_dropped_silently_rather_than_refused(monkeypatch, tmp_path):
    """Documents current behaviour: a typo'd mmproj path yields a text-only
    server with no warning, so vision decode fails much later and elsewhere."""
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: True)
    rec = _popen_recorder(monkeypatch)
    out = mr.start_tier(
        "full",
        model_path=str(_model(tmp_path)),
        mmproj_path=str(tmp_path / "typo.gguf"),
        runtime_binary=str(_binary(tmp_path)),
    )
    assert out["ok"] is True
    assert "--mmproj" not in rec["cmd"]


def test_a_child_that_exits_early_is_reported_with_its_code(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: pytest.fail("probed a dead child"))
    _popen_recorder(monkeypatch, proc=FakeProc(alive=False, returncode=3))
    out = mr.start_tier(
        "medium",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
    )
    assert out["ok"] is False
    assert "exited early (code 3)" in out["error"]
    assert mr._tier_procs["medium"] is None


def test_a_healthy_start_returns_the_base_url_and_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: True)
    proc = FakeProc(alive=True, pid=777)
    _popen_recorder(monkeypatch, proc=proc)
    out = mr.start_tier(
        "full",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
    )
    assert out == {
        "ok": True,
        "already_running": False,
        "base_url": "http://127.0.0.1:8740/v1",
        "tier": "full",
        "pid": 777,
    }
    assert mr._tier_procs["full"] is proc
    assert mr._tier_last_used["full"] > 0


def test_an_open_port_alone_is_not_enough_to_declare_the_start_healthy(monkeypatch, tmp_path):
    """The port binds well before the model finishes loading."""
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _popen_recorder(monkeypatch)
    out = mr.start_tier(
        "medium",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
        wait_s=0.05,
    )
    assert out["ok"] is False
    assert out["starting"] is True
    assert "not ready yet" in out["error"]


def test_a_slow_tier_is_left_loading_and_picked_up_by_the_next_call_without_a_second_spawn(
    monkeypatch, tmp_path
):
    """Every caller uses the default wait_s. Killing a child that was merely
    still loading when the clock ran out meant a tier slower than wait_s could
    never come up: each attempt spawned, waited, killed, and reported failure.
    The child stays registered as "starting"; the next call waits on *it*."""
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    probes = {"n": 0}

    def _port_open(*a, **k):
        probes["n"] += 1
        return probes["n"] > 3  # ready only on the second start_tier call

    monkeypatch.setattr(mr, "_port_open", _port_open)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: True)
    proc = FakeProc(alive=True, pid=991)
    spawns = []

    def _popen(cmd, **kwargs):
        spawns.append(cmd)
        return proc

    monkeypatch.setattr(subprocess, "Popen", _popen)
    kw = {"model_path": str(_model(tmp_path)), "runtime_binary": str(_binary(tmp_path))}

    first = mr.start_tier("medium", wait_s=0.0, **kw)
    assert first["ok"] is False
    assert first["starting"] is True
    assert first["pid"] == 991
    assert not (proc.terminated or proc.killed), "a loading child was killed"
    assert mr._tier_procs["medium"] is proc, "the loading child must stay registered"

    second = mr.start_tier("medium", wait_s=5.0, **kw)
    assert second["ok"] is True
    assert second["pid"] == 991
    assert second["already_running"] is True
    assert len(spawns) == 1, "a second llama-server was spawned beside the first"
    assert mr._tier_last_used["medium"] > 0


def test_a_registered_child_that_died_while_loading_is_cleared_and_respawned(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mr, "_health", lambda *a, **k: True)
    corpse = FakeProc(alive=False, returncode=-9, pid=1)
    mr._tier_procs["medium"] = corpse
    fresh = FakeProc(alive=True, pid=2)
    rec = _popen_recorder(monkeypatch, proc=fresh)
    out = mr.start_tier(
        "medium",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
    )
    assert out["ok"] is True
    assert out["pid"] == 2
    assert "cmd" in rec
    assert mr._tier_procs["medium"] is fresh


def test_a_child_that_exits_during_a_later_wait_is_reported_and_unregistered(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: False)
    proc = FakeProc(alive=True)
    _popen_recorder(monkeypatch, proc=proc)
    kw = {"model_path": str(_model(tmp_path)), "runtime_binary": str(_binary(tmp_path))}
    assert mr.start_tier("full", wait_s=0.0, **kw)["starting"] is True
    proc._alive = False
    proc.returncode = 7
    out = mr.start_tier("full", wait_s=0.0, **kw)
    assert out["ok"] is False
    assert "exited early (code 7)" in out["error"]
    assert mr._tier_procs["full"] is None


def test_there_is_never_more_than_one_popen_per_tier(monkeypatch, tmp_path):
    """Repeated starts against a tier that never becomes ready must not stack
    up servers — that was the VRAM leak the old timeout-kill existed to stop."""
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(mr, "_port_open", lambda *a, **k: False)
    spawned = []

    def _popen(cmd, **kwargs):
        spawned.append(FakeProc(alive=True, pid=100 + len(spawned)))
        return spawned[-1]

    monkeypatch.setattr(subprocess, "Popen", _popen)
    kw = {"model_path": str(_model(tmp_path)), "runtime_binary": str(_binary(tmp_path))}
    for _ in range(5):
        out = mr.start_tier("medium", wait_s=0.0, **kw)
        assert out["ok"] is False and out["starting"] is True
    assert len(spawned) == 1
    assert mr._tier_procs["medium"] is spawned[0]
    assert not spawned[0].terminated and not spawned[0].killed


def test_a_slot_cleared_by_a_concurrent_stop_aborts_the_start(monkeypatch, tmp_path):
    """stop_tier from another thread nulls the slot; the wait loop must notice
    instead of dereferencing None."""
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)

    class RacySlots(dict):
        def get(self, key, default=None):
            return None  # another thread nulled the slot right after Popen

    monkeypatch.setattr(mr, "_tier_procs", RacySlots(light=None, medium=None, full=None))
    _popen_recorder(monkeypatch)
    out = mr.start_tier(
        "medium",
        model_path=str(_model(tmp_path)),
        runtime_binary=str(_binary(tmp_path)),
        wait_s=1.0,
    )
    assert out["ok"] is False
    assert "proc cleared during start" in out["error"]


# --------------------------------------------------------------------------
# ensure_tier
# --------------------------------------------------------------------------


def test_ensuring_a_running_tier_does_not_start_anything(monkeypatch):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: True)
    monkeypatch.setattr(mr, "start_tier", lambda *a, **k: pytest.fail("restarted a live tier"))
    out = mr.ensure_tier("light", model_path="x", runtime_binary="y")
    assert out == {"ok": True, "base_url": "http://127.0.0.1:8741/v1", "tier": "light"}
    assert mr._tier_last_used["light"] > 0


def test_ensuring_a_stopped_tier_forwards_every_argument(monkeypatch):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    seen = {}

    def _start(name, **kwargs):
        seen["name"] = name
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mr, "start_tier", _start)
    mr.ensure_tier(
        "full",
        model_path="m.gguf",
        mmproj_path="mm.gguf",
        runtime_binary="ls.exe",
        n_gpu_layers=8,
    )
    assert seen == {
        "name": "full",
        "model_path": "m.gguf",
        "mmproj_path": "mm.gguf",
        "runtime_binary": "ls.exe",
        "n_gpu_layers": 8,
    }


# --------------------------------------------------------------------------
# stop_all_tiers / tier_status
# --------------------------------------------------------------------------


def test_stopping_everything_covers_every_tier_including_the_resident_one(monkeypatch):
    stopped = []
    monkeypatch.setattr(mr, "stop_tier", lambda n: stopped.append(n) or {"ok": True, "tier": n})
    out = mr.stop_all_tiers()
    assert stopped == list(MDL_TIERS)
    assert "light" in stopped  # shutdown must not spare the resident tier
    assert out["ok"] is True
    assert set(out["tiers"]) == set(MDL_TIERS)


def test_status_reports_every_tier_and_hides_the_url_when_down(monkeypatch):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: n == "medium")
    mr._tier_last_used["medium"] = 123.0
    out = mr.tier_status()
    assert out["ok"] is True
    assert set(out["tiers"]) == set(MDL_TIERS)
    assert out["tiers"]["medium"] == {
        "running": True,
        "base_url": "http://127.0.0.1:8742/v1",
        "last_used": 123.0,
    }
    assert out["tiers"]["light"]["base_url"] is None
    assert out["tiers"]["full"]["running"] is False


def test_status_survives_a_tier_that_was_never_used(monkeypatch):
    monkeypatch.setattr(mr, "is_tier_running", lambda n: False)
    monkeypatch.setattr(mr, "_tier_last_used", {})
    assert mr.tier_status()["tiers"]["light"]["last_used"] == 0


# --------------------------------------------------------------------------
# ensure_idle_watcher
# --------------------------------------------------------------------------


class FakeThread:
    instances: list[FakeThread] = []

    def __init__(self, target=None, name=None, daemon=None):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        FakeThread.instances.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def fake_threads(monkeypatch):
    FakeThread.instances = []
    monkeypatch.setattr(threading, "Thread", FakeThread)
    return FakeThread.instances


def test_the_watcher_thread_is_a_daemon_so_it_cannot_block_shutdown(fake_threads):
    mr.ensure_idle_watcher(resident_first=False)
    assert len(fake_threads) == 1
    assert fake_threads[0].daemon is True
    assert fake_threads[0].started is True
    assert fake_threads[0].name == "remedy-mdl-idle-stop"


def test_a_second_call_does_not_start_a_second_watcher(fake_threads, monkeypatch):
    monkeypatch.setattr(mr, "start_tier", lambda *a, **k: pytest.fail("started twice"))
    mr.ensure_idle_watcher(resident_first=False)
    mr.ensure_idle_watcher(resident_first=False)
    mr.ensure_idle_watcher(resident_first=True)
    assert len(fake_threads) == 1


def test_the_watcher_loop_skips_resident_tiers(fake_threads, monkeypatch):
    reaped = []
    monkeypatch.setattr(mr, "tier_idle_stop", lambda n: reaped.append(n))

    def _sleep(_s):
        if len(reaped) >= 2:
            raise _Stop

    monkeypatch.setattr(time, "sleep", _sleep)
    mr.ensure_idle_watcher(resident_first=False)
    with pytest.raises(_Stop):
        fake_threads[0].target()
    assert "light" not in reaped
    assert set(reaped) == {"medium", "full"}


def test_a_failing_reap_does_not_kill_the_watcher(fake_threads, monkeypatch):
    ticks = []

    def _boom(_name):
        raise ValueError("reap exploded")

    monkeypatch.setattr(mr, "tier_idle_stop", _boom)

    def _sleep(_s):
        ticks.append(1)
        if len(ticks) > 2:
            raise _Stop

    monkeypatch.setattr(time, "sleep", _sleep)
    mr.ensure_idle_watcher(resident_first=False)
    with pytest.raises(_Stop):
        fake_threads[0].target()
    assert len(ticks) == 3  # kept ticking after the exception


def test_resident_first_starts_the_light_tier_from_vision_config(fake_threads, monkeypatch, tmp_path):
    from remedy.vision import config as vconfig

    rb = _binary(tmp_path)
    monkeypatch.setattr(
        vconfig,
        "load_vision_json",
        lambda: {
            "model_path": str(_model(tmp_path)),
            "mmproj_path": str(tmp_path / "mm.gguf"),
            "runtime_binary": str(rb),
        },
    )
    seen = {}
    monkeypatch.setattr(mr, "start_tier", lambda name, **kw: seen.update({"name": name, **kw}))
    mr.ensure_idle_watcher(resident_first=True)
    assert seen["name"] == "light"
    assert seen["runtime_binary"] == str(rb)


def test_a_stale_runtime_binary_in_config_falls_back_to_the_installed_one(
    fake_threads, monkeypatch, tmp_path
):
    from remedy.vision import config as vconfig
    from remedy.vision import install as vinstall

    installed = _binary(tmp_path, "installed.exe")
    monkeypatch.setattr(
        vconfig,
        "load_vision_json",
        lambda: {"model_path": str(_model(tmp_path)), "runtime_binary": str(tmp_path / "gone.exe")},
    )
    monkeypatch.setattr(vinstall, "runtime_binary_path", lambda: installed)
    seen = {}
    monkeypatch.setattr(mr, "start_tier", lambda name, **kw: seen.update(kw))
    mr.ensure_idle_watcher(resident_first=True)
    assert seen["runtime_binary"] == str(installed)
    assert seen["mmproj_path"] is None


@pytest.mark.parametrize(
    "vstate",
    [
        {},
        {"model_path": None, "runtime_binary": None},
        {"model_path": "m.gguf"},  # no binary anywhere
    ],
)
def test_an_unconfigured_vision_stack_starts_nothing(fake_threads, monkeypatch, vstate):
    from remedy.vision import config as vconfig
    from remedy.vision import install as vinstall

    monkeypatch.setattr(vconfig, "load_vision_json", lambda: dict(vstate))
    monkeypatch.setattr(vinstall, "runtime_binary_path", lambda: None)
    monkeypatch.setattr(mr, "start_tier", lambda *a, **k: pytest.fail("started an unconfigured tier"))
    mr.ensure_idle_watcher(resident_first=True)
    assert fake_threads[0].started is True


def test_a_broken_vision_config_does_not_take_down_the_watcher(fake_threads, monkeypatch):
    from remedy.vision import config as vconfig

    def _boom():
        raise RuntimeError("vision.json unreadable")

    monkeypatch.setattr(vconfig, "load_vision_json", _boom)
    mr.ensure_idle_watcher(resident_first=True)  # must not raise
    assert fake_threads[0].started is True
