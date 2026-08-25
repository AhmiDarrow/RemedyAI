"""The supervisor for the local vision decoder (llama-server).

This module decides three things that can hurt the owner if they go wrong.

*Where it will talk.* ``_health`` probes a base URL that comes out of
``vision.json`` — a file on disk that a bad installer, a stale profile or an
attacker with write access can point anywhere. If the loopback guard is ever
skipped, Remedy sends the owner's screenshots to whatever host that file names.
So the probe must fail closed on a LAN address, on cloud metadata, on a
``file:`` URL, on credentials in the URL, and it must not follow a redirect
that a local listener uses to bounce it off-machine.

*What it will kill.* ``stop_server`` terminates a PID it read back from
``vision.json``. PIDs get recycled; the number in that file may belong to the
owner's editor by the time Remedy quits. Killing a stranger is the failure
mode, and the only thing standing in the way is the "does this look like
llama-server" check.

*What it will start.* ``start_server`` spawns a real process. It must refuse
when RMB already owns the local host, when the model or projector file is
missing, and when the binary cannot be found — and when the child dies during
startup it must say so rather than block the desktop for the full wait window.

The cheap-probe caching underneath all of this exists because Settings polls
``is_running`` constantly; a cache that answers with the wrong key, or a probe
that urlopens a dead port, freezes the UI for seconds at a time.
"""

from __future__ import annotations

import socket
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest

from remedy.vision import config as vision_config
from remedy.vision import runtime as vr
from remedy.vision.config import load_vision_json, save_vision_json
from tests.harness.fake_http import fake_http_fixture  # noqa: F401

# --------------------------------------------------------------------------
# isolation: this module is a pile of process-wide globals
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_runtime_globals():
    """Save/restore every module global so one test cannot leak into the next."""
    saved = (
        vr._proc,
        vr._last_used,
        dict(vr._running_cache),
        dict(vr._vision_json_cache),
        vr._idle_thread_started,
    )
    vr._proc = None
    vr._last_used = 0.0
    vr._running_cache.update({"ts": 0.0, "value": False, "key": ""})
    vr._vision_json_cache.update({"path": "", "mtime": -1.0, "size": -1, "data": {}})
    try:
        yield
    finally:
        vr._proc = saved[0]
        vr._last_used = saved[1]
        vr._running_cache.clear()
        vr._running_cache.update(saved[2])
        vr._vision_json_cache.clear()
        vr._vision_json_cache.update(saved[3])
        vr._idle_thread_started = saved[4]


class _SubprocessShim:
    """Stand-in for the ``subprocess`` module inside ``vision.runtime`` only.

    Spawning is an assertion failure unless a test opts in by assigning
    ``Popen``/``run``. This is what keeps a bug in the code under test from
    launching a real llama-server or a real taskkill on the owner's machine.
    """

    DEVNULL = subprocess.DEVNULL
    CREATE_NO_WINDOW = 0x08000000
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self) -> None:
        self.popen_calls: list[dict[str, Any]] = []
        self.run_calls: list[list[str]] = []

    def Popen(self, *args: Any, **kwargs: Any):  # noqa: N802 - mirrors subprocess
        # Record first: a test asserting "nothing was spawned" then has real
        # evidence rather than the absence of a crash.
        self.popen_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("subprocess.Popen must not be reached in this test")

    def run(self, *args: Any, **kwargs: Any):
        self.run_calls.append(list(args[0]) if args else [])
        raise AssertionError("subprocess.run must not be reached in this test")


@pytest.fixture(autouse=True)
def no_real_spawn(monkeypatch) -> _SubprocessShim:
    """No test in this module may spawn a real process by accident."""
    shim = _SubprocessShim()
    monkeypatch.setattr(vr, "subprocess", shim)
    return shim


@pytest.fixture(autouse=True)
def no_real_idle_threads(monkeypatch):
    """The MDL watcher spawns its own 30s daemon loop; never let it start here."""
    import remedy.runtime.mdl_runtime as mdl

    monkeypatch.setattr(mdl, "ensure_idle_watcher", lambda *a, **k: None)


class _FakeProc:
    """Just enough Popen for the supervisor: poll(), pid, terminate/kill/wait."""

    def __init__(self, pid: int = 4242, poll_results: list[int | None] | None = None) -> None:
        self.pid = pid
        self._poll = list(poll_results or [])
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.waits: list[float | None] = []
        self.wait_raises = False

    def poll(self) -> int | None:
        if self._poll:
            self.returncode = self._poll.pop(0)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self.wait_raises and not self.killed:
            raise subprocess.TimeoutExpired(cmd="llama-server", timeout=timeout or 0)
        self.returncode = 0
        return 0


def _closed_port(host: str = "127.0.0.1") -> int:
    """An ephemeral port that was bound and released — nothing listens there."""
    sock = socket.socket()
    try:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _write_state(home: Path, **fields: Any) -> Path:
    state: dict[str, Any] = {"host": "127.0.0.1", "port": _closed_port()}
    state.update(fields)
    save_vision_json(state, home)
    return home


# --------------------------------------------------------------------------
# _port_open
# --------------------------------------------------------------------------


def test_a_listening_loopback_port_is_reported_open(fake_http) -> None:
    assert vr._port_open(fake_http.host, fake_http.port) is True


def test_a_released_port_is_reported_closed() -> None:
    assert vr._port_open("127.0.0.1", _closed_port()) is False


def test_port_open_returns_false_instead_of_raising_on_a_bad_host() -> None:
    """A garbage host in vision.json must not raise out of a status poll."""
    assert vr._port_open("this-host-does-not-resolve.invalid", 9) is False


# --------------------------------------------------------------------------
# _health — the loopback guard is the whole point
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        "",
        "   ",
        "http://169.254.169.254/v1",  # cloud metadata
        "http://10.0.0.5:8740/v1",  # LAN
        "http://192.168.1.20:8740/v1",
        "http://[fe80::1]:8740/v1",
        "file:///c:/windows/system32/config",
        "ftp://127.0.0.1/v1",
        "http://user:pass@127.0.0.1:8740/v1",  # userinfo
        "http://2130706433:8740/v1",  # decimal-encoded 127.0.0.1
        "http://0x7f000001:8740/v1",  # hex-encoded
        "http://0.0.0.0:8740/v1",
    ],
)
def test_health_refuses_a_non_loopback_base_url_without_any_request(
    base: str, monkeypatch
) -> None:
    """The guard must run *before* the request, not after it."""
    import remedy.core.security as security

    def explode(*_a: object, **_k: object) -> None:
        raise AssertionError(f"urlopen must not be reached for {base!r}")

    monkeypatch.setattr(security, "urlopen_no_redirect", explode)
    assert vr._health(base) is False


def test_health_accepts_a_two_hundred_from_the_models_endpoint(fake_http) -> None:
    fake_http.route("/models", json={"data": []})
    assert vr._health(fake_http.base_url) is True
    assert len(fake_http.requests_for("/models", method="GET")) == 1


def test_health_sends_a_remedy_user_agent(fake_http) -> None:
    fake_http.route("/models", json={"data": []})
    vr._health(fake_http.base_url)
    request = fake_http.last_request
    assert request is not None
    assert (request.header("user-agent") or "").startswith("RemedyAI-vision/")


def test_health_falls_back_to_the_v1_models_endpoint(fake_http) -> None:
    """Some llama.cpp builds only expose /v1/models; /models 404s."""
    fake_http.route("/v1/models", json={"data": []})
    assert vr._health(fake_http.base_url) is True
    assert len(fake_http.requests_for("/models")) == 1
    assert len(fake_http.requests_for("/v1/models")) == 1


def test_health_probes_a_v1_base_url_only_at_v1_models(fake_http) -> None:
    fake_http.route("/v1/models", json={"data": []})
    assert vr._health(fake_http.base_url + "/v1") is True
    assert len(fake_http.requests_for("/v1/models")) == 1
    assert fake_http.requests_for("/models") == []


def test_a_v1_base_url_retries_the_same_path_when_it_fails(fake_http) -> None:
    """Documents the fallback's dead end: for a /v1 base both probes are equal."""
    fake_http.route("/v1/models", status=500, json={"error": "boom"})
    assert vr._health(fake_http.base_url + "/v1") is False
    assert len(fake_http.requests_for("/v1/models")) == 2


def test_health_ignores_a_trailing_slash_on_the_base_url(fake_http) -> None:
    fake_http.route("/models", json={"data": []})
    assert vr._health(fake_http.base_url + "/") is True
    assert len(fake_http.requests_for("/models")) == 1


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_any_two_hundred_status_counts_as_healthy(fake_http, status: int) -> None:
    fake_http.route("/models", status=status, body="")
    assert vr._health(fake_http.base_url) is True


@pytest.mark.parametrize("status", [400, 401, 404, 500, 503])
def test_a_non_two_hundred_status_is_not_healthy(fake_http, status: int) -> None:
    fake_http.route("*", status=status, body="nope")
    assert vr._health(fake_http.base_url) is False


def test_health_does_not_follow_a_redirect_off_the_machine(fake_http) -> None:
    """A local listener must not be able to bounce the probe at the LAN."""
    fake_http.route(
        "*",
        status=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        body="",
    )
    assert vr._health(fake_http.base_url) is False
    # Both attempts hit *this* server; neither chased the Location header.
    assert len(fake_http.requests) == 2


def test_a_dropped_connection_is_not_healthy(fake_http) -> None:
    fake_http.route("*", drop=True)
    assert vr._health(fake_http.base_url) is False


def test_a_hung_server_times_out_rather_than_blocking_forever(fake_http) -> None:
    fake_http.route("*", hang=True)
    assert vr._health(fake_http.base_url, timeout=0.2) is False


def test_health_of_a_dead_port_is_false() -> None:
    port = _closed_port()
    assert vr._health(f"http://127.0.0.1:{port}/v1", timeout=0.2) is False


# --------------------------------------------------------------------------
# _load_vision_json_cached
# --------------------------------------------------------------------------


def test_a_missing_vision_json_loads_as_an_empty_dict(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert vr._load_vision_json_cached(home) == {}
    assert vr._vision_json_cache["mtime"] == -1.0
    assert vr._vision_json_cache["size"] == -1


def test_corrupt_vision_json_loads_as_an_empty_dict_not_an_exception(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "vision").mkdir(parents=True)
    (home / "vision" / "vision.json").write_text("{not json", encoding="utf-8")
    assert vr._load_vision_json_cached(home) == {}


def test_an_unchanged_file_is_served_from_cache(tmp_path: Path, monkeypatch) -> None:
    home = _write_state(tmp_path / "home", port=18742)
    first = vr._load_vision_json_cached(home)
    assert first["port"] == 18742

    monkeypatch.setattr(
        vr,
        "load_vision_json",
        lambda _hd=None: (_ for _ in ()).throw(AssertionError("re-read the file")),
    )
    assert vr._load_vision_json_cached(home)["port"] == 18742


def test_a_rewritten_file_invalidates_the_cache(tmp_path: Path) -> None:
    home = _write_state(tmp_path / "home", port=18742)
    assert vr._load_vision_json_cached(home)["port"] == 18742
    save_vision_json({"host": "127.0.0.1", "port": 19999}, home)
    assert vr._load_vision_json_cached(home)["port"] == 19999


def test_the_caller_cannot_poison_the_cache_by_mutating_the_result(tmp_path: Path) -> None:
    """A shallow copy is handed out; a caller stashing junk must not persist."""
    home = _write_state(tmp_path / "home", port=18742)
    got = vr._load_vision_json_cached(home)
    got["port"] = 1
    got["injected"] = True
    again = vr._load_vision_json_cached(home)
    assert again["port"] == 18742
    assert "injected" not in again


def test_a_stat_failure_falls_back_to_reading_the_file(tmp_path: Path, monkeypatch) -> None:
    """A locked/denied vision.json must still be read, just never cached."""

    class _StatDenied:
        def __str__(self) -> str:
            return "denied-vision.json"

        def is_file(self) -> bool:
            return True

        def stat(self) -> Any:
            raise OSError("stat denied")

    monkeypatch.setattr(vision_config, "vision_json_path", lambda _hd=None: _StatDenied())
    monkeypatch.setattr(vr, "load_vision_json", lambda _hd=None: {"port": 4321})
    assert vr._load_vision_json_cached(tmp_path)["port"] == 4321
    assert vr._vision_json_cache["mtime"] == -1.0


def test_switching_home_dir_does_not_reuse_the_other_homes_state(tmp_path: Path) -> None:
    a = _write_state(tmp_path / "a", port=11111)
    b = _write_state(tmp_path / "b", port=22222)
    assert vr._load_vision_json_cached(a)["port"] == 11111
    assert vr._load_vision_json_cached(b)["port"] == 22222
    assert vr._load_vision_json_cached(a)["port"] == 11111


# --------------------------------------------------------------------------
# is_running
# --------------------------------------------------------------------------


@pytest.fixture
def probes(monkeypatch):
    """Recorded, controllable ``_port_open``/``_health`` for is_running tests."""
    calls: dict[str, list[Any]] = {"port": [], "health": []}
    state = {"port_open": False, "healthy": False}

    def _port_open(host: str, port: int, timeout: float = 0.15) -> bool:
        calls["port"].append((host, port))
        return bool(state["port_open"])

    def _health(base: str, timeout: float = 0.35) -> bool:
        calls["health"].append(base)
        return bool(state["healthy"])

    monkeypatch.setattr(vr, "_port_open", _port_open)
    monkeypatch.setattr(vr, "_health", _health)
    return types.SimpleNamespace(calls=calls, state=state)


def test_nothing_listening_is_not_running_and_never_urlopens(tmp_path: Path, probes) -> None:
    """The dead-port urlopen was seconds of UI freeze; it must not come back."""
    home = _write_state(tmp_path / "home")
    assert vr.is_running(home) is False
    assert probes.calls["health"] == []


def test_a_child_that_is_not_listening_yet_is_not_running(tmp_path: Path, probes) -> None:
    home = _write_state(tmp_path / "home")
    vr._proc = _FakeProc()
    probes.state["port_open"] = False
    assert vr.is_running(home) is False
    assert probes.calls["health"] == []


def test_our_own_child_on_an_open_port_is_running_without_an_http_probe(
    tmp_path: Path, probes
) -> None:
    """Weights can take a minute to load; don't stall the desktop on /models."""
    home = _write_state(tmp_path / "home")
    vr._proc = _FakeProc()
    probes.state["port_open"] = True
    assert vr.is_running(home) is True
    assert probes.calls["health"] == []


def test_require_http_forces_a_health_probe_on_our_own_child(tmp_path: Path, probes) -> None:
    home = _write_state(tmp_path / "home")
    vr._proc = _FakeProc()
    probes.state["port_open"] = True
    probes.state["healthy"] = False
    assert vr.is_running(home, force=True, require_http=True) is False
    assert probes.calls["health"] != []


def test_a_dead_child_handle_does_not_count_as_alive(tmp_path: Path, probes) -> None:
    home = _write_state(tmp_path / "home")
    vr._proc = _FakeProc(poll_results=[7])
    probes.state["port_open"] = False
    assert vr.is_running(home) is False


def test_an_open_port_with_no_child_reads_as_running_even_when_unhealthy(
    tmp_path: Path, probes
) -> None:
    """Documents the UI rule: an orphan llama-server counts while it listens."""
    home = _write_state(tmp_path / "home")
    probes.state["port_open"] = True
    probes.state["healthy"] = False
    assert vr.is_running(home) is True


def test_require_http_makes_an_orphan_prove_itself(tmp_path: Path, probes) -> None:
    home = _write_state(tmp_path / "home")
    probes.state["port_open"] = True
    probes.state["healthy"] = False
    assert vr.is_running(home, force=True, require_http=True) is False
    probes.state["healthy"] = True
    assert vr.is_running(home, force=True, require_http=True) is True


def test_a_repeat_call_is_answered_from_cache_without_probing(
    tmp_path: Path, probes, monkeypatch
) -> None:
    home = _write_state(tmp_path / "home")
    probes.state["port_open"] = True
    assert vr.is_running(home) is True

    def explode(*_a: object, **_k: object) -> bool:
        raise AssertionError("cached call must not probe")

    monkeypatch.setattr(vr, "_port_open", explode)
    assert vr.is_running(home) is True


def test_force_bypasses_the_probe_cache(tmp_path: Path, probes) -> None:
    home = _write_state(tmp_path / "home")
    probes.state["port_open"] = True
    assert vr.is_running(home) is True
    probes.state["port_open"] = False
    assert vr.is_running(home) is True  # still cached
    assert vr.is_running(home, force=True) is False


def test_a_port_change_invalidates_the_cache(tmp_path: Path, probes) -> None:
    """A cache keyed only on time would answer for the *previous* decoder."""
    home = tmp_path / "home"
    _write_state(home, port=11111, base_url="http://127.0.0.1:11111/v1")
    probes.state["port_open"] = True
    assert vr.is_running(home) is True

    probes.state["port_open"] = False
    save_vision_json(
        {"host": "127.0.0.1", "port": 22222, "base_url": "http://127.0.0.1:22222/v1"}, home
    )
    assert vr.is_running(home) is False
    assert probes.calls["port"][-1] == ("127.0.0.1", 22222)


def test_invalidate_running_cache_clears_both_caches(tmp_path: Path, probes) -> None:
    home = _write_state(tmp_path / "home")
    probes.state["port_open"] = True
    assert vr.is_running(home) is True
    vr.invalidate_running_cache()
    assert vr._running_cache["key"] == ""
    assert vr._running_cache["value"] is False
    assert vr._vision_json_cache["data"] == {}
    probes.state["port_open"] = False
    assert vr.is_running(home) is False


def test_a_garbage_port_in_vision_json_raises_rather_than_probing_the_default(
    tmp_path: Path, probes
) -> None:
    """int('abc') is not silently swallowed — a broken config must be visible."""
    home = _write_state(tmp_path / "home", port="abc")
    with pytest.raises(ValueError):
        vr.is_running(home)


def test_an_open_port_is_reported_running_over_a_real_socket(
    tmp_path: Path, fake_http
) -> None:
    """No probe doubles: real TCP, real HTTP, real vision.json."""
    home = tmp_path / "home"
    fake_http.route("/v1/models", status=500, json={"error": "loading"})
    _write_state(
        home,
        host=fake_http.host,
        port=fake_http.port,
        base_url=fake_http.base_url + "/v1",
    )
    assert vr.is_running(home, force=True) is True
    assert vr.is_running(home, force=True, require_http=True) is False

    fake_http.route("/v1/models", json={"data": [{"id": "smolvlm2"}]})
    assert vr.is_running(home, force=True, require_http=True) is True


# --------------------------------------------------------------------------
# mark_used / last_used_age_s
# --------------------------------------------------------------------------


def test_never_used_reports_none_rather_than_zero() -> None:
    vr._last_used = 0.0
    assert vr.last_used_age_s() is None


def test_mark_used_makes_the_age_small_and_real() -> None:
    vr.mark_used()
    age = vr.last_used_age_s()
    assert age is not None
    assert 0.0 <= age < 5.0


def test_a_clock_that_jumps_backwards_never_yields_a_negative_age(monkeypatch) -> None:
    """A negative idle age would read as 'just used' forever."""
    vr._last_used = 10_000_000_000.0  # far future
    assert vr.last_used_age_s() == 0.0


# --------------------------------------------------------------------------
# maybe_idle_stop
# --------------------------------------------------------------------------


@pytest.fixture
def idle_env(monkeypatch):
    """is_running/stop_server doubles so the watchdog can be driven directly."""
    calls: list[Any] = []
    state = {"running": True}

    def _stop(home_dir: Any = None) -> dict[str, Any]:
        calls.append(home_dir)
        return {"ok": True, "stopped": True, "pids": [1]}

    monkeypatch.setattr(vr, "is_running", lambda *a, **k: bool(state["running"]))
    monkeypatch.setattr(vr, "stop_server", _stop)
    return types.SimpleNamespace(stops=calls, state=state)


def test_idle_stop_disabled_by_zero_never_stops_the_server(idle_env) -> None:
    out = vr.maybe_idle_stop(None, idle_stop_s=0)
    assert out == {"ok": True, "stopped": False, "reason": "disabled"}
    assert idle_env.stops == []


def test_a_negative_idle_limit_also_disables_the_watchdog(idle_env) -> None:
    assert vr.maybe_idle_stop(None, idle_stop_s=-5)["reason"] == "disabled"
    assert idle_env.stops == []


def test_a_server_that_is_not_running_is_not_stopped_again(idle_env) -> None:
    idle_env.state["running"] = False
    out = vr.maybe_idle_stop(None, idle_stop_s=600)
    assert out["reason"] == "not_running"
    assert idle_env.stops == []


def test_the_first_tick_marks_use_instead_of_killing_a_fresh_server(idle_env) -> None:
    """Without this, a server started seconds ago is shot on the first poll."""
    vr._last_used = 0.0
    out = vr.maybe_idle_stop(None, idle_stop_s=600)
    assert out["reason"] == "first_mark"
    assert idle_env.stops == []
    assert vr.last_used_age_s() is not None


def test_a_recently_used_server_is_left_alone(idle_env) -> None:
    vr.mark_used()
    out = vr.maybe_idle_stop(None, idle_stop_s=600)
    assert out["reason"] == "active"
    assert out["limit_s"] == 600
    assert out["idle_s"] < 5
    assert idle_env.stops == []


def test_an_idle_server_is_stopped_and_the_reason_is_reported(idle_env) -> None:
    import time as _time

    vr._last_used = _time.time() - 900
    out = vr.maybe_idle_stop("home", idle_stop_s=600)
    assert out["ok"] is True
    assert out["stopped"] is True
    assert out["reason"] == "idle_timeout"
    assert out["idle_s"] >= 900
    assert idle_env.stops == ["home"]


def test_the_idle_limit_is_read_from_vision_json_when_not_given(
    tmp_path: Path, idle_env
) -> None:
    home = _write_state(tmp_path / "home", idle_stop_s=1234)
    vr.mark_used()
    out = vr.maybe_idle_stop(home)
    assert out["reason"] == "active"
    assert out["limit_s"] == 1234


def test_a_zero_in_vision_json_does_not_disable_the_watchdog(tmp_path: Path, idle_env) -> None:
    """Documents a sharp edge: ``idle_stop_s: 0`` on disk reads as the default 600.

    The value is loaded with ``or 600``, so only the explicit keyword argument
    can switch the watchdog off. Anyone editing vision.json to keep the model
    resident is not getting what they asked for.
    """
    home = _write_state(tmp_path / "home", idle_stop_s=0)
    vr.mark_used()
    out = vr.maybe_idle_stop(home)
    assert out["reason"] == "active"
    assert out["limit_s"] == 600


@pytest.mark.parametrize("bad", ["abc", {"x": 1}, object()])
def test_a_garbage_idle_limit_falls_back_to_the_default_rather_than_disabling(
    idle_env, bad: Any
) -> None:
    """Silently turning the watchdog off would leak GPU memory for ever."""
    vr.mark_used()
    out = vr.maybe_idle_stop(None, idle_stop_s=bad)
    assert out["reason"] == "active"
    assert out["limit_s"] == 600


@pytest.mark.parametrize("falsy", [[], "", 0.0])
def test_a_falsy_idle_limit_is_read_as_zero_and_disables_the_watchdog(
    idle_env, falsy: Any
) -> None:
    """Documents the other half of the coercion: falsy is 0, and 0 means off."""
    vr.mark_used()
    assert vr.maybe_idle_stop(None, idle_stop_s=falsy)["reason"] == "disabled"
    assert idle_env.stops == []


def test_an_unreadable_vision_json_still_uses_the_default_limit(monkeypatch, idle_env) -> None:
    monkeypatch.setattr(
        vr,
        "load_vision_json",
        lambda _hd=None: (_ for _ in ()).throw(OSError("gone")),
    )
    vr.mark_used()
    out = vr.maybe_idle_stop(None)
    assert out["reason"] == "active"
    assert out["limit_s"] == 600


# --------------------------------------------------------------------------
# ensure_idle_watcher / reset_idle_watcher
# --------------------------------------------------------------------------


class _ThreadRecorder:
    """Replaces ``threading`` inside the module so no real loop thread starts."""

    def __init__(self) -> None:
        self.created: list[Any] = []

    def Thread(self, *, target, name, daemon):  # noqa: N802 - mirrors threading
        rec = types.SimpleNamespace(target=target, name=name, daemon=daemon, started=False)
        rec.start = lambda: setattr(rec, "started", True)
        self.created.append(rec)
        return rec


def test_the_idle_watcher_starts_exactly_one_daemon_thread(monkeypatch) -> None:
    threads = _ThreadRecorder()
    monkeypatch.setattr(vr, "threading", threads)
    vr._idle_thread_started = False

    vr.ensure_idle_watcher(None)
    vr.ensure_idle_watcher(None)
    vr.ensure_idle_watcher(None)

    assert len(threads.created) == 1
    assert threads.created[0].daemon is True
    assert threads.created[0].started is True
    assert threads.created[0].name == "remedy-local-idle-stop"


def test_reset_idle_watcher_allows_a_watcher_after_a_restart(monkeypatch) -> None:
    threads = _ThreadRecorder()
    monkeypatch.setattr(vr, "threading", threads)
    vr._idle_thread_started = False

    vr.ensure_idle_watcher(None)
    vr.reset_idle_watcher()
    vr.ensure_idle_watcher(None)
    assert len(threads.created) == 2


def test_a_broken_mdl_watcher_does_not_stop_the_vision_watcher(monkeypatch) -> None:
    import remedy.runtime.mdl_runtime as mdl

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("mdl is unavailable")

    monkeypatch.setattr(mdl, "ensure_idle_watcher", boom)
    threads = _ThreadRecorder()
    monkeypatch.setattr(vr, "threading", threads)
    vr._idle_thread_started = False

    vr.ensure_idle_watcher(None)
    assert len(threads.created) == 1


# --------------------------------------------------------------------------
# start_server
# --------------------------------------------------------------------------


@pytest.fixture
def startable(tmp_path: Path, monkeypatch):
    """A home whose vision.json points at real (dummy) model/mmproj/binary files."""
    import remedy.runtime.rmb.mode as rmb_mode

    home = tmp_path / "home"
    vision = home / "vision"
    runtime = vision / "runtime"
    runtime.mkdir(parents=True)
    model = vision / "model.gguf"
    model.write_bytes(b"GGUF-model")
    mmproj = vision / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF-mmproj")
    binary = runtime / "llama-server.exe"
    binary.write_bytes(b"MZ")

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)
    monkeypatch.setattr(vr, "ensure_idle_watcher", lambda *a, **k: None)

    port = _closed_port()
    save_vision_json(
        {
            "host": "127.0.0.1",
            "port": port,
            "base_url": f"http://127.0.0.1:{port}/v1",
            "model_id": "smolvlm2-2.2b",
            "model_path": str(model),
            "mmproj_path": str(mmproj),
            "runtime_binary": str(binary),
        },
        home,
    )
    return types.SimpleNamespace(
        home=home, model=model, mmproj=mmproj, binary=binary, port=port
    )


def test_start_server_refuses_while_rmb_owns_the_local_host(
    startable, monkeypatch, no_real_spawn
) -> None:
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: True)
    out = vr.start_server(home_dir=startable.home)
    assert out["ok"] is False
    assert out["skipped"] is True
    assert out["reason"] == "rmb_exclusive_host"
    assert no_real_spawn.popen_calls == []


def test_ignore_rmb_lets_a_cpu_only_observe_wake_through(
    startable, monkeypatch, no_real_spawn
) -> None:
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: True)
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)
    spawned: list[list[str]] = []

    def spawn(cmd: list[str], **_k: Any) -> Any:
        spawned.append(list(cmd))
        return _FakeProc()

    no_real_spawn.Popen = spawn
    out = vr.start_server(home_dir=startable.home, wait_s=0.01, ignore_rmb=True)
    # Past both RMB guards: it really did spawn, and only then timed out.
    assert len(spawned) == 1
    assert out["ok"] is False
    assert out.get("reason") != "rmb_exclusive_host"


def test_rmb_starting_mid_path_aborts_the_spawn(startable, monkeypatch, no_real_spawn) -> None:
    """The second guard exists because RMB can come up while we validate files."""
    import remedy.runtime.rmb.mode as rmb_mode

    answers = iter([False, True])
    monkeypatch.setattr(
        rmb_mode, "should_skip_vision_stack", lambda _cfg=None: next(answers, True)
    )
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)

    out = vr.start_server(home_dir=startable.home, wait_s=0.01)
    assert out["skipped"] is True
    assert out["reason"] == "rmb_exclusive_host"
    assert no_real_spawn.popen_calls == []


def test_a_missing_model_file_is_reported_not_raised(startable, no_real_spawn) -> None:
    startable.model.unlink()
    state = load_vision_json(startable.home)
    state["model_path"] = str(startable.model)
    save_vision_json(state, startable.home)
    # Bundle activation is what would normally rescue this; make it fail.
    import remedy.runtime.bundle as bundle

    out = _with_failed_bundle(bundle, lambda: vr.start_server(home_dir=startable.home))
    assert out["ok"] is False
    assert "missing" in out["error"].lower() or "not activated" in out["error"].lower()
    assert no_real_spawn.popen_calls == []


def _with_failed_bundle(bundle_mod: Any, fn: Any) -> Any:
    original = bundle_mod.activate_local_bundle
    bundle_mod.activate_local_bundle = lambda *a, **k: {"ok": False, "error": "no bundle"}
    try:
        return fn()
    finally:
        bundle_mod.activate_local_bundle = original


def test_a_missing_mmproj_file_is_reported_not_raised(startable, no_real_spawn) -> None:
    startable.mmproj.unlink()
    out = vr.start_server(home_dir=startable.home)
    assert out["ok"] is False
    assert "mmproj" in out["error"]
    assert no_real_spawn.popen_calls == []


def test_no_vision_json_and_no_bundle_is_an_error_not_a_spawn(
    tmp_path: Path, monkeypatch, no_real_spawn
) -> None:
    import remedy.runtime.bundle as bundle
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)
    monkeypatch.setattr(
        bundle, "activate_local_bundle", lambda *a, **k: {"ok": False, "error": "no bundle"}
    )
    out = vr.start_server(home_dir=tmp_path / "empty")
    assert out["ok"] is False
    assert out["error"] == "no bundle"
    assert no_real_spawn.popen_calls == []


def test_a_bundle_activation_crash_is_reported_not_raised(
    tmp_path: Path, monkeypatch, no_real_spawn
) -> None:
    import remedy.runtime.bundle as bundle
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("zip is corrupt")

    monkeypatch.setattr(bundle, "activate_local_bundle", boom)
    out = vr.start_server(home_dir=tmp_path / "empty")
    assert out["ok"] is False
    assert "Bundle activate failed" in out["error"]
    assert "zip is corrupt" in out["error"]


def test_a_retired_model_pin_is_migrated_instead_of_raising_key_error(
    startable, monkeypatch, no_real_spawn
) -> None:
    """A stale qwen pin in vision.json must not become KeyError spam in logs."""
    from remedy.vision.catalog import DEFAULT_MODEL_ID

    state = load_vision_json(startable.home)
    state["model_id"] = "qwen2.5-vl-3b"
    save_vision_json(state, startable.home)

    import remedy.runtime.bundle as bundle

    monkeypatch.setattr(
        bundle, "activate_local_bundle", lambda *a, **k: {"ok": False, "error": "no bundle"}
    )
    out = vr.start_server(home_dir=startable.home)

    migrated = load_vision_json(startable.home)
    assert migrated["model_id"] == DEFAULT_MODEL_ID
    assert "model_path" not in migrated
    assert "mmproj_path" not in migrated
    assert out["ok"] is False  # paths were cleared, so it cannot start
    assert no_real_spawn.popen_calls == []


def test_a_missing_binary_is_reported_not_raised(startable, monkeypatch, no_real_spawn) -> None:
    import remedy.runtime.bundle as bundle

    state = load_vision_json(startable.home)
    state.pop("runtime_binary", None)
    save_vision_json(state, startable.home)
    startable.binary.unlink()

    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(bundle, "runtime_binary_from_bundle", lambda _rid: None)

    out = vr.start_server(home_dir=startable.home)
    assert out == {"ok": False, "error": "llama-server binary not found"}
    assert no_real_spawn.popen_calls == []


def test_an_already_healthy_server_is_not_started_twice(tmp_path: Path, fake_http, monkeypatch):
    """Real socket, real HTTP: an answering decoder short-circuits the spawn."""
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)
    monkeypatch.setattr(vr, "ensure_idle_watcher", lambda *a, **k: None)

    home = tmp_path / "home"
    vision = home / "vision"
    vision.mkdir(parents=True)
    model = vision / "m.gguf"
    model.write_bytes(b"x")
    mmproj = vision / "p.gguf"
    mmproj.write_bytes(b"x")
    fake_http.route("/v1/models", json={"data": []})
    save_vision_json(
        {
            "host": fake_http.host,
            "port": fake_http.port,
            "base_url": fake_http.base_url + "/v1",
            "model_path": str(model),
            "mmproj_path": str(mmproj),
        },
        home,
    )

    out = vr.start_server(home_dir=home)
    assert out["ok"] is True
    assert out["already_running"] is True
    assert out["base_url"] == fake_http.base_url + "/v1"
    assert out["pid"] is None  # we do not own this process
    assert vr.last_used_age_s() is not None


def test_a_failed_spawn_is_reported_not_raised(startable, monkeypatch, no_real_spawn) -> None:
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("Access is denied")

    no_real_spawn.Popen = boom
    out = vr.start_server(home_dir=startable.home)
    assert out["ok"] is False
    assert "Failed to start llama-server" in out["error"]
    assert "Access is denied" in out["error"]


def test_a_child_that_exits_early_is_reported_with_its_code(
    startable, monkeypatch, no_real_spawn
) -> None:
    """Otherwise the caller waits the full 60s for a process already gone."""
    proc = _FakeProc(poll_results=[3])
    no_real_spawn.Popen = lambda *a, **k: proc
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)

    out = vr.start_server(home_dir=startable.home, wait_s=30)
    assert out["ok"] is False
    assert "exited early" in out["error"]
    assert "3" in out["error"]


def test_a_concurrent_stop_during_startup_is_reported_not_ignored(
    startable, monkeypatch, no_real_spawn
) -> None:
    no_real_spawn.Popen = lambda *a, **k: _FakeProc()
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)

    # stop_server lands between the spawn and the first wait-loop tick.
    original_invalidate = vr.invalidate_running_cache

    def concurrent_stop() -> None:
        vr._proc = None
        original_invalidate()

    monkeypatch.setattr(vr, "invalidate_running_cache", concurrent_stop)

    out = vr.start_server(home_dir=startable.home, wait_s=30)
    assert out["ok"] is False
    assert "cleared during start" in out["error"]


def test_a_server_that_never_listens_times_out_with_the_wait_window(
    startable, monkeypatch, no_real_spawn
) -> None:
    no_real_spawn.Popen = lambda *a, **k: _FakeProc()
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)

    out = vr.start_server(home_dir=startable.home, wait_s=0.05)
    assert out["ok"] is False
    assert "did not become healthy within 0.05s" in out["error"]
    assert vr._running_cache["key"] == ""


def test_a_successful_start_records_the_pid_and_the_command(
    tmp_path: Path, fake_http, monkeypatch
) -> None:
    """End to end over a real socket: spawn, wait for /v1/models, persist pid."""
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)
    monkeypatch.setattr(vr, "ensure_idle_watcher", lambda *a, **k: None)
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)

    home = tmp_path / "home"
    vision = home / "vision"
    (vision / "runtime").mkdir(parents=True)
    model = vision / "m.gguf"
    model.write_bytes(b"x")
    mmproj = vision / "p.gguf"
    mmproj.write_bytes(b"x")
    binary = vision / "runtime" / "llama-server.exe"
    binary.write_bytes(b"MZ")
    fake_http.route("/v1/models", json={"data": []})
    save_vision_json(
        {
            "host": fake_http.host,
            "port": fake_http.port,
            "base_url": fake_http.base_url + "/v1",
            "model_path": str(model),
            "mmproj_path": str(mmproj),
            "runtime_binary": str(binary),
        },
        home,
    )

    spawned: dict[str, Any] = {}

    def spawn(cmd: list[str], **kwargs: Any) -> Any:
        spawned["cmd"] = list(cmd)
        spawned["kwargs"] = kwargs
        return _FakeProc(pid=31337)

    import remedy.vision.runtime as _vr

    _vr.subprocess.Popen = spawn  # type: ignore[attr-defined]

    out = vr.start_server(home_dir=home, n_gpu_layers=0, wait_s=5)
    assert out["ok"] is True
    assert out["already_running"] is False
    assert out["pid"] == 31337

    cmd = spawned["cmd"]
    assert cmd[0] == str(binary)
    assert cmd[cmd.index("-m") + 1] == str(model)
    assert cmd[cmd.index("--mmproj") + 1] == str(mmproj)
    assert cmd[cmd.index("--host") + 1] == fake_http.host
    assert cmd[cmd.index("--port") + 1] == str(fake_http.port)
    assert cmd[cmd.index("--ctx-size") + 1] == "4096"
    assert cmd[cmd.index("-ngl") + 1] == "0"
    # stdout/stderr must be discarded, or a full pipe buffer deadlocks the child.
    assert spawned["kwargs"]["stdout"] == subprocess.DEVNULL
    assert spawned["kwargs"]["stderr"] == subprocess.DEVNULL
    assert spawned["kwargs"]["cwd"] == str(binary.parent)

    assert load_vision_json(home)["pid"] == 31337


def test_a_busy_unhealthy_port_makes_the_server_move_to_a_free_one(
    tmp_path: Path, fake_http, monkeypatch
) -> None:
    """Another program on 8740 must not make Remedy fight it for the port."""
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)
    monkeypatch.setattr(vr, "ensure_idle_watcher", lambda *a, **k: None)
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)

    home = tmp_path / "home"
    vision = home / "vision"
    (vision / "runtime").mkdir(parents=True)
    model = vision / "m.gguf"
    model.write_bytes(b"x")
    mmproj = vision / "p.gguf"
    mmproj.write_bytes(b"x")
    binary = vision / "runtime" / "llama-server.exe"
    binary.write_bytes(b"MZ")
    # Port is open but the thing answering is not a healthy llama-server.
    fake_http.route("*", status=500, body="not llama")
    save_vision_json(
        {
            "host": fake_http.host,
            "port": fake_http.port,
            "base_url": fake_http.base_url + "/v1",
            "model_path": str(model),
            "mmproj_path": str(mmproj),
            "runtime_binary": str(binary),
        },
        home,
    )

    import remedy.vision.runtime as _vr

    _vr.subprocess.Popen = lambda *a, **k: _FakeProc()  # type: ignore[attr-defined]

    out = vr.start_server(home_dir=home, wait_s=0.01)
    assert out["ok"] is False  # nothing ever listens on the new port
    moved = load_vision_json(home)
    assert moved["port"] != fake_http.port
    assert fake_http.port < moved["port"] <= fake_http.port + 19
    assert moved["base_url"] == f"http://{fake_http.host}:{moved['port']}/v1"


def test_a_broken_rmb_check_does_not_block_the_decoder(
    startable, monkeypatch, no_real_spawn
) -> None:
    """If the exclusivity check itself crashes, vision must still be startable."""
    import remedy.runtime.rmb.mode as rmb_mode

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("rmb.json is corrupt")

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", boom)
    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)
    spawned: list[Any] = []
    no_real_spawn.Popen = lambda *a, **k: spawned.append(a) or _FakeProc()

    out = vr.start_server(home_dir=startable.home, wait_s=0.01)
    assert len(spawned) == 1
    assert out.get("reason") != "rmb_exclusive_host"


def test_a_successful_bundle_activation_rebinds_the_paths(
    tmp_path: Path, monkeypatch, no_real_spawn
) -> None:
    """The no-network recovery path: activate the prebundled stack and re-read."""
    import remedy.runtime.bundle as bundle
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)
    home = tmp_path / "home"
    model = tmp_path / "bundled.gguf"
    model.write_bytes(b"x")

    def activate(hd: Any = None, enabled: bool = True) -> dict[str, Any]:
        save_vision_json(
            {
                "host": "127.0.0.1",
                "port": _closed_port(),
                "model_path": str(model),
                "mmproj_path": str(tmp_path / "absent.gguf"),
            },
            hd,
        )
        return {"ok": True}

    monkeypatch.setattr(bundle, "activate_local_bundle", activate)
    out = vr.start_server(home_dir=home)
    # Rebound far enough to reach the mmproj check rather than dying earlier.
    assert out["ok"] is False
    assert "mmproj file missing" in out["error"]
    assert no_real_spawn.popen_calls == []


def test_a_bundle_that_reports_ok_but_writes_nothing_is_reported(
    tmp_path: Path, monkeypatch, no_real_spawn
) -> None:
    import remedy.runtime.bundle as bundle
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "should_skip_vision_stack", lambda _cfg=None: False)
    monkeypatch.setattr(bundle, "activate_local_bundle", lambda *a, **k: {"ok": True})
    out = vr.start_server(home_dir=tmp_path / "empty")
    assert out == {"ok": False, "error": "Local model not ready (no vision.json)"}
    assert no_real_spawn.popen_calls == []


def test_a_crashing_bundle_binary_lookup_is_reported_as_binary_not_found(
    startable, monkeypatch, no_real_spawn
) -> None:
    import remedy.runtime.bundle as bundle

    state = load_vision_json(startable.home)
    state.pop("runtime_binary", None)
    save_vision_json(state, startable.home)
    startable.binary.unlink()

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("bundle root vanished")

    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(bundle, "runtime_binary_from_bundle", boom)

    out = vr.start_server(home_dir=startable.home)
    assert out == {"ok": False, "error": "llama-server binary not found"}
    assert no_real_spawn.popen_calls == []


def test_the_binary_beside_the_runtime_dir_is_used_when_vision_json_has_no_pin(
    startable, monkeypatch, no_real_spawn
) -> None:
    state = load_vision_json(startable.home)
    state.pop("runtime_binary", None)
    save_vision_json(state, startable.home)

    monkeypatch.setattr(vr, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(vr, "_port_open", lambda *a, **k: False)
    spawned: list[list[str]] = []
    no_real_spawn.Popen = lambda cmd, **k: spawned.append(list(cmd)) or _FakeProc()

    vr.start_server(home_dir=startable.home, wait_s=0.01)
    assert spawned and spawned[0][0] == str(startable.binary)


# --------------------------------------------------------------------------
# _kill_pid_tree / _pid_is_alive / _looks_like_llama_server
# --------------------------------------------------------------------------


class _FakeOS:
    """Module-local ``os`` stand-in so posix branches are reachable on Windows."""

    def __init__(self, name: str = "posix") -> None:
        self.name = name
        self.signals: list[tuple[int, int]] = []
        self.alive = True
        self.environ: dict[str, str] = {}

    def kill(self, pid: int, sig: int) -> None:
        self.signals.append((pid, sig))
        if sig == 0 and not self.alive:
            raise ProcessLookupError(pid)


@pytest.mark.parametrize("pid", [0, -1, -9999])
def test_a_nonpositive_pid_is_never_killed(pid: int, no_real_spawn) -> None:
    """pid 0 means 'this process group' on posix — killing it kills Remedy."""
    assert vr._kill_pid_tree(pid) is False
    assert no_real_spawn.run_calls == []


def test_kill_pid_tree_on_windows_uses_a_force_tree_taskkill(monkeypatch, no_real_spawn) -> None:
    seen: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        seen.append(list(args))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    no_real_spawn.run = fake_run
    monkeypatch.setattr(vr, "os", _FakeOS(name="nt"))

    assert vr._kill_pid_tree(12345, force=True) is True
    assert seen == [["taskkill", "/F", "/PID", "12345", "/T"]]


def test_a_graceful_kill_omits_the_force_flag(monkeypatch, no_real_spawn) -> None:
    seen: list[list[str]] = []
    no_real_spawn.run = lambda args, **k: seen.append(list(args)) or types.SimpleNamespace()
    monkeypatch.setattr(vr, "os", _FakeOS(name="nt"))

    vr._kill_pid_tree(777, force=False)
    assert seen == [["taskkill", "/PID", "777", "/T"]]


def test_a_taskkill_timeout_is_reported_as_not_killed(monkeypatch, no_real_spawn) -> None:
    def timeout(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=15)

    no_real_spawn.run = timeout
    monkeypatch.setattr(vr, "os", _FakeOS(name="nt"))
    assert vr._kill_pid_tree(999) is False


def test_kill_pid_tree_on_posix_escalates_to_sigkill(monkeypatch) -> None:
    fake_os = _FakeOS(name="posix")
    monkeypatch.setattr(vr, "os", fake_os)
    monkeypatch.setattr(vr.time, "sleep", lambda _s: None)

    assert vr._kill_pid_tree(4321, force=True) is True
    assert (4321, 15) in fake_os.signals
    assert (4321, 9) in fake_os.signals


def test_kill_pid_tree_on_posix_does_not_sigkill_a_process_that_already_died(
    monkeypatch,
) -> None:
    fake_os = _FakeOS(name="posix")
    fake_os.alive = False
    monkeypatch.setattr(vr, "os", fake_os)
    monkeypatch.setattr(vr.time, "sleep", lambda _s: None)

    vr._kill_pid_tree(4321, force=True)
    assert (4321, 9) not in fake_os.signals


@pytest.mark.parametrize("pid", [0, -3])
def test_a_nonpositive_pid_is_never_alive(pid: int) -> None:
    assert vr._pid_is_alive(pid) is False


def test_pid_is_alive_reports_a_missing_process_as_dead(monkeypatch) -> None:
    fake_os = _FakeOS(name="posix")
    fake_os.alive = False
    monkeypatch.setattr(vr, "os", fake_os)
    assert vr._pid_is_alive(31337) is False


def test_pid_is_alive_reports_a_live_process(monkeypatch) -> None:
    monkeypatch.setattr(vr, "os", _FakeOS(name="posix"))
    assert vr._pid_is_alive(31337) is True


@pytest.mark.parametrize("pid", [0, -1])
def test_a_nonpositive_pid_never_looks_like_llama_server(pid: int) -> None:
    assert vr._looks_like_llama_server(pid) is False


def test_a_posix_cmdline_naming_llama_server_is_recognised(monkeypatch) -> None:
    class _Cmdline:
        def __init__(self, _p: str) -> None:
            pass

        def read_bytes(self) -> bytes:
            return b"/opt/llama/llama-server\x00-m\x00model.gguf"

    monkeypatch.setattr(vr, "os", _FakeOS(name="posix"))
    monkeypatch.setattr(vr, "Path", _Cmdline)
    assert vr._looks_like_llama_server(1234) is True


def test_a_posix_cmdline_naming_something_else_is_refused(monkeypatch) -> None:
    class _Cmdline:
        def __init__(self, _p: str) -> None:
            pass

        def read_bytes(self) -> bytes:
            return b"/usr/bin/code\x00--wait"

    monkeypatch.setattr(vr, "os", _FakeOS(name="posix"))
    monkeypatch.setattr(vr, "Path", _Cmdline)
    assert vr._looks_like_llama_server(1234) is False


def test_a_windows_process_named_llama_server_is_recognised(monkeypatch, no_real_spawn) -> None:
    no_real_spawn.run = lambda *a, **k: types.SimpleNamespace(
        stdout="llama-server\r\n", stderr="", returncode=0
    )
    monkeypatch.setattr(vr, "os", _FakeOS(name="nt"))
    assert vr._looks_like_llama_server(4242) is True


def test_a_windows_process_named_anything_else_is_refused(monkeypatch, no_real_spawn) -> None:
    """This is the check that stops Remedy killing the owner's editor."""
    no_real_spawn.run = lambda *a, **k: types.SimpleNamespace(
        stdout="Code\r\n", stderr="", returncode=0
    )
    monkeypatch.setattr(vr, "os", _FakeOS(name="nt"))
    assert vr._looks_like_llama_server(4242) is False


def test_a_dead_windows_pid_produces_empty_output_and_is_refused(
    monkeypatch, no_real_spawn
) -> None:
    no_real_spawn.run = lambda *a, **k: types.SimpleNamespace(
        stdout="", stderr="", returncode=0
    )
    monkeypatch.setattr(vr, "os", _FakeOS(name="nt"))
    assert vr._looks_like_llama_server(4242) is False


def test_a_slow_process_lookup_fails_open_and_allows_the_kill(
    monkeypatch, no_real_spawn
) -> None:
    """Fail-open here is deliberate but load-bearing: pair it with _pid_is_alive."""

    def timeout(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=5)

    no_real_spawn.run = timeout
    monkeypatch.setattr(vr, "os", _FakeOS(name="nt"))
    assert vr._looks_like_llama_server(4242) is True


def test_an_unreadable_proc_cmdline_fails_open_and_allows_the_kill(monkeypatch) -> None:
    class _Denied:
        def __init__(self, _p: str) -> None:
            pass

        def read_bytes(self) -> bytes:
            raise OSError("permission denied")

    monkeypatch.setattr(vr, "os", _FakeOS(name="posix"))
    monkeypatch.setattr(vr, "Path", _Denied)
    assert vr._looks_like_llama_server(1234) is True


def test_the_windows_liveness_check_is_a_signal_zero_probe(monkeypatch) -> None:
    """Pins the call shape, because on Windows the signal number is not inert.

    CPython maps ``os.kill(pid, sig)`` on Windows onto ``TerminateProcess``
    for every signal except ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT`` and the
    special-cased 0. Verified against this interpreter: signal 0 really is a
    query and leaves the target running. Any future edit that changes the
    literal 0 here turns a liveness check into a kill, so the number is
    asserted rather than assumed.
    """
    fake_os = _FakeOS(name="nt")
    monkeypatch.setattr(vr, "os", fake_os)
    assert vr._pid_is_alive(31337) is True
    assert fake_os.signals == [(31337, 0)]


@pytest.mark.parametrize(
    "error", [PermissionError("access denied"), OSError("bad parameter"), SystemError("wat")]
)
def test_a_pid_we_cannot_open_is_reported_dead(monkeypatch, error: Exception) -> None:
    """Documents a false negative: a live llama-server owned by another account.

    ``os.kill(pid, 0)`` on Windows opens the process with elevated rights, so a
    process Remedy has no rights over raises PermissionError and reads here as
    "already gone" — stop_server will then leave it running rather than say so.
    """

    class _Denied(_FakeOS):
        def kill(self, pid: int, sig: int) -> None:
            raise error

    monkeypatch.setattr(vr, "os", _Denied(name="nt"))
    assert vr._pid_is_alive(31337) is False


def test_a_windows_liveness_check_on_a_missing_pid_reports_dead(monkeypatch) -> None:
    fake_os = _FakeOS(name="nt")
    fake_os.alive = False
    monkeypatch.setattr(vr, "os", fake_os)
    assert vr._pid_is_alive(31337) is False


# --------------------------------------------------------------------------
# stop_server
# --------------------------------------------------------------------------


def test_stop_server_kills_a_child_that_ignores_terminate(tmp_path: Path, monkeypatch) -> None:
    home = _write_state(tmp_path / "home")
    proc = _FakeProc(pid=555)
    proc.wait_raises = True
    vr._proc = proc
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: False)

    out = vr.stop_server(home_dir=home)
    assert proc.terminated is True
    assert proc.killed is True
    assert out["stopped"] is True
    assert vr._proc is None


def test_stop_server_is_a_no_op_when_nothing_is_running(tmp_path: Path, monkeypatch) -> None:
    home = _write_state(tmp_path / "home")
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: False)
    out = vr.stop_server(home_dir=home)
    assert out == {"ok": True, "stopped": False, "pids": []}


def test_stop_server_does_not_kill_a_recorded_pid_that_is_already_gone(
    tmp_path: Path, monkeypatch
) -> None:
    home = _write_state(tmp_path / "home", pid=98765)
    killed: list[int] = []
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(
        vr, "_looks_like_llama_server", lambda _pid: pytest.fail("must not identify a dead pid")
    )
    monkeypatch.setattr(vr, "_kill_pid_tree", lambda pid, force=True: killed.append(pid) or True)

    out = vr.stop_server(home_dir=home)
    assert killed == []
    assert out["stopped"] is False


def test_stop_server_refuses_to_kill_a_recycled_pid_that_is_not_llama_server(
    tmp_path: Path, monkeypatch
) -> None:
    """PIDs get reused; vision.json may name the owner's editor by now."""
    home = _write_state(tmp_path / "home", pid=4242)
    killed: list[int] = []
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(vr, "_looks_like_llama_server", lambda _pid: False)
    monkeypatch.setattr(vr, "_windows_process_name", lambda _pid: "code")
    monkeypatch.setattr(vr, "_kill_pid_tree", lambda pid, force=True: killed.append(pid) or True)

    out = vr.stop_server(home_dir=home)
    assert killed == []
    assert out["stopped"] is False
    assert out["pids"] == [4242]


def test_stop_server_kills_our_own_child_pid_without_the_name_check(
    tmp_path: Path, monkeypatch
) -> None:
    """We spawned it, so its identity is not in doubt even if lookup would fail."""
    home = _write_state(tmp_path / "home")
    proc = _FakeProc(pid=606)
    proc.wait_raises = False
    vr._proc = proc
    killed: list[int] = []
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(
        vr,
        "_looks_like_llama_server",
        lambda _pid: pytest.fail("our own pid must not need identification"),
    )
    monkeypatch.setattr(vr, "_kill_pid_tree", lambda pid, force=True: killed.append(pid) or True)

    out = vr.stop_server(home_dir=home)
    assert killed == [606]
    assert out["pids"] == [606]


def test_stop_server_kills_each_pid_once(tmp_path: Path, monkeypatch) -> None:
    home = _write_state(tmp_path / "home", pid=606)
    proc = _FakeProc(pid=606)
    vr._proc = proc
    killed: list[int] = []
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(vr, "_looks_like_llama_server", lambda _pid: True)
    monkeypatch.setattr(vr, "_kill_pid_tree", lambda pid, force=True: killed.append(pid) or True)

    out = vr.stop_server(home_dir=home)
    assert killed == [606]
    assert out["pids"] == [606]


def test_stop_server_clears_the_recorded_pid_from_vision_json(
    tmp_path: Path, monkeypatch
) -> None:
    """A stale pid left behind is exactly what gets a stranger killed later."""
    home = _write_state(tmp_path / "home", pid=4242, port=18765)
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(vr, "_looks_like_llama_server", lambda _pid: True)
    monkeypatch.setattr(vr, "_kill_pid_tree", lambda _pid, force=True: True)

    vr.stop_server(home_dir=home)
    state = load_vision_json(home)
    assert "pid" not in state
    assert state["port"] == 18765  # the rest of the file survives


def test_stop_server_survives_a_garbage_pid_in_vision_json(tmp_path: Path, monkeypatch) -> None:
    home = _write_state(tmp_path / "home", pid="not-a-pid")
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(vr, "_looks_like_llama_server", lambda _pid: True)
    monkeypatch.setattr(vr, "_kill_pid_tree", lambda _pid, force=True: True)

    out = vr.stop_server(home_dir=home)
    assert out["ok"] is True
    assert out["pids"] == []


def test_stop_server_invalidates_the_running_cache(tmp_path: Path, monkeypatch, probes) -> None:
    home = _write_state(tmp_path / "home")
    probes.state["port_open"] = True
    assert vr.is_running(home) is True
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: False)

    vr.stop_server(home_dir=home)
    probes.state["port_open"] = False
    assert vr.is_running(home) is False


# --------------------------------------------------------------------------
# shutdown_vision_for_exit / _pid
# --------------------------------------------------------------------------


def test_shutdown_for_exit_returns_the_stop_result(tmp_path: Path, monkeypatch) -> None:
    home = _write_state(tmp_path / "home")
    monkeypatch.setattr(vr, "_pid_is_alive", lambda _pid: False)
    out = vr.shutdown_vision_for_exit(home_dir=home)
    assert out["ok"] is True


def test_shutdown_for_exit_never_raises_out_of_process_teardown(monkeypatch) -> None:
    """This runs from atexit / lifespan; an exception there loses the shutdown."""

    def boom(**_k: object) -> None:
        raise RuntimeError("registry gone")

    monkeypatch.setattr(vr, "stop_server", boom)
    out = vr.shutdown_vision_for_exit()
    assert out["ok"] is False
    assert out["stopped"] is False
    assert "registry gone" in out["error"]


def test_pid_is_none_when_no_child_is_owned() -> None:
    vr._proc = None
    assert vr._pid() is None


def test_pid_is_none_for_a_child_that_has_already_exited() -> None:
    vr._proc = _FakeProc(pid=808, poll_results=[0])
    assert vr._pid() is None


def test_pid_is_reported_for_a_live_child() -> None:
    vr._proc = _FakeProc(pid=808)
    assert vr._pid() == 808
