"""RMB service: the supervisor that owns the local llama-server process.

This module decides when to spawn a GGUF host, when to refuse, when a port is
safe to free, and when a "healthy" answer is a lie. If it is wrong the owner
sees one of four failures, none of which look like a bug in this file:

- Remedy kills a process that was never llama-server (a stale pid in rmb.json
  pointing at whatever the OS recycled that number onto).
- Remedy calls a half-loaded host "running", the next chat turn gets HTTP 503,
  and the user is told the model is broken.
- The user presses Stop and a background watchdog quietly starts it again.
- A settings patch with junk in it silently rewrites rmb.json, and the host
  never comes back.

So the tests below lean on the refusals: the guards that return False, the
paths that must not shell out, the pid the process must never kill (its own),
and the bits on disk that must survive.

Nothing here spawns a process or frees a real port — the autouse fixture below
replaces both with doubles and fails the test if anything reaches subprocess.
Health probes go over a real loopback socket (tests.harness.fake_http) so the
loopback guard, the headers and the timeout are exercised for real.
"""

from __future__ import annotations

import os
import socket
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest

from remedy.runtime.rmb import service as svc
from remedy.runtime.rmb.config import (
    DEFAULT_CHAT_PORT,
    load_rmb_json,
    merge_state,
    models_dir,
    save_rmb_json,
)
from tests.harness.fake_http import fake_http_fixture  # noqa: F401

# Captured before the autouse fixture swaps them for doubles: the few tests
# that exercise these functions themselves need the originals, and must not
# accidentally get the recorder installed for everyone else.
_REAL_KILL_PID = svc._kill_pid
_REAL_KILL_LISTENERS = svc._kill_listeners_on_port
_REAL_FIND_PID = svc._find_pid_on_port
_REAL_LOOKS_LIKE_LLAMA = svc._looks_like_llama_server

_ON_WINDOWS = os.name == "nt"
_needs_windows = pytest.mark.skipif(
    not _ON_WINDOWS, reason="netstat/powershell branches only run on Windows"
)


class _Guard:
    """Records what the module *would* have destroyed, and destroys nothing."""

    def __init__(self) -> None:
        self.killed_pids: list[int] = []
        self.killed_ports: list[int] = []

    def kill_pid(self, pid: int) -> bool:
        self.killed_pids.append(int(pid))
        return True

    def kill_listeners(self, port: int) -> int:
        self.killed_ports.append(int(port))
        return 0


@pytest.fixture(autouse=True)
def rmb_guard(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate module globals and make destructive calls impossible.

    ``service`` keeps the child handle, the health cache, the start-flight
    latch and the user-stopped bit in module globals. Leaking any of them into
    the next test produces a pass that means nothing.
    """
    guard = _Guard()

    def _no_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"test tried to spawn a real process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    monkeypatch.setattr(svc, "_kill_pid", guard.kill_pid)
    monkeypatch.setattr(svc, "_kill_listeners_on_port", guard.kill_listeners)
    # A real watchdog thread outlives the test and polls the owner's :8787.
    monkeypatch.setattr(svc, "ensure_rmb_watchdog", lambda *a, **k: None)
    # Never register an atexit hook that would stop a host at interpreter exit.
    monkeypatch.setattr(svc, "_atexit_registered", True)

    svc._proc = None
    svc._user_stopped = False
    svc._starting_until = 0.0
    svc._loading_since = 0.0
    svc._last_start_error = None
    svc._last_health_detail = ""
    svc._start_flight_active = False
    svc._start_flight_result = None
    svc._start_flight_event.set()
    svc._watchdog_restart_times = []
    svc._watchdog_fail_streak = 0
    svc._spec_cap_cache.clear()
    svc._flag_cap_cache.clear()
    svc._discover_ggufs_cache.update({"ts": 0.0, "key": "", "value": []})
    svc.invalidate_cache()
    yield guard
    svc._proc = None
    svc._user_stopped = False
    svc._starting_until = 0.0
    svc._loading_since = 0.0
    svc._start_flight_active = False
    svc._discover_ggufs_cache.update({"ts": 0.0, "key": "", "value": []})
    svc.invalidate_cache()


def _seed(home: Path, **overrides: Any) -> dict[str, Any]:
    """Write an rmb.json under *home* and return the merged state."""
    state = merge_state(dict(overrides))
    save_rmb_json(state, str(home))
    return state


def _closed_port() -> int:
    """A port nothing is listening on (bound then released)."""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _fake_proc(*, alive: bool, pid: int = 4242, args: Any = None) -> Any:
    return types.SimpleNamespace(
        poll=lambda: None if alive else 0,
        returncode=None if alive else 0,
        pid=pid,
        args=args if args is not None else ["llama-server"],
    )


def _completed(stdout: str = "", returncode: int = 0) -> Any:
    return types.SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


# --------------------------------------------------------------------------
# _health — "is the host answering model queries", not "is the port open"
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "   ",
        "http://10.0.0.5:8787/v1",  # LAN literal: no DNS, and must be refused
        "http://[fd00::1]:8787/v1",
        "ftp://127.0.0.1:8787/v1",
        "http://user:pw@127.0.0.1:8787/v1",
    ],
)
def test_a_non_loopback_base_url_is_refused_before_any_request(base_url: str) -> None:
    assert svc._health(base_url) is False
    assert svc._last_health_detail == "bad_url"


def test_health_endpoint_returning_ok_means_ready(fake_http: Any) -> None:
    fake_http.route("/health", json={"status": "ok"})
    assert svc._health(fake_http.url("/v1")) is True
    assert svc._last_health_detail == "ok"
    assert fake_http.requests_for("/health")


def test_health_probe_identifies_itself_as_remedy(fake_http: Any) -> None:
    fake_http.route("/health", json={"status": "ok"})
    svc._health(fake_http.url("/v1"))
    assert fake_http.last_request.header("user-agent") == "RemedyAI-RMB/1.0"


def test_v1_models_is_only_tried_when_health_is_missing(fake_http: Any) -> None:
    """A 404 on /health is an old build, not a failure — fall through."""
    fake_http.route("/v1/models", json={"data": [{"id": "qwopus"}]})
    assert svc._health(fake_http.url("/v1")) is True
    assert fake_http.requests_for("/health")
    assert fake_http.requests_for("/v1/models")


def test_health_503_loading_model_is_not_ready(fake_http: Any) -> None:
    fake_http.route("/health", status=503, json={"error": "Loading model"})
    assert svc._health(fake_http.url("/v1")) is False
    assert svc._last_health_detail == "loading"


def test_a_200_body_that_says_loading_is_not_ready(fake_http: Any) -> None:
    fake_http.route("/health", body="loading model, please wait")
    assert svc._health(fake_http.url("/v1")) is False
    assert svc._last_health_detail == "loading"


def test_an_error_body_on_health_falls_through_to_models(fake_http: Any) -> None:
    fake_http.route("/health", json={"status": "error"})
    fake_http.route("/v1/models", json={"data": [{"id": "qwopus"}]})
    assert svc._health(fake_http.url("/v1")) is True


def test_a_500_from_both_endpoints_is_reported_not_raised(fake_http: Any) -> None:
    fake_http.route("*", status=500, body="boom")
    assert svc._health(fake_http.url("/v1")) is False
    assert svc._last_health_detail == "http_500"


def test_a_refused_connection_is_reported_not_raised() -> None:
    assert svc._health(f"http://127.0.0.1:{_closed_port()}/v1") is False
    assert svc._last_health_detail.startswith("urlerr:")


def test_health_gives_up_at_its_timeout_rather_than_hanging(fake_http: Any) -> None:
    fake_http.route("/health", hang=True)
    fake_http.route("/v1/models", hang=True)
    assert svc._health(fake_http.url("/v1"), timeout=0.3) is False
    assert svc._last_health_detail != "ok"


# --------------------------------------------------------------------------
# port / running / loading state
# --------------------------------------------------------------------------


def test_port_open_distinguishes_a_live_socket_from_a_closed_one(fake_http: Any) -> None:
    assert svc._port_open(fake_http.host, fake_http.port) is True
    assert svc._port_open("127.0.0.1", _closed_port()) is False


def test_an_open_port_alone_does_not_count_as_running(tmp_path: Path, fake_http: Any) -> None:
    """Exclusive-host safety: something else on :8787 must not read as RMB."""
    _seed(tmp_path, host=fake_http.host, port=fake_http.port)
    fake_http.route("*", status=404, body="not llama")
    assert svc.is_running(str(tmp_path), force=True, require_http=False) is False
    assert svc.is_running(str(tmp_path), force=True, require_http=True) is False


def test_is_running_serves_a_cached_answer_until_invalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, port=_closed_port())
    probes: list[int] = []

    def _probe(host: str, port: int, timeout: float = 0.15) -> bool:
        probes.append(port)
        return False

    monkeypatch.setattr(svc, "_port_open", _probe)
    assert svc.is_running(str(tmp_path)) is False
    assert svc.is_running(str(tmp_path)) is False
    assert len(probes) == 1
    svc.invalidate_cache()
    assert svc.is_running(str(tmp_path)) is False
    assert len(probes) == 2


def test_a_dead_managed_child_drops_the_cache_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this the UI keeps saying "up" for a couple of seconds after a crash."""
    _seed(tmp_path, port=_closed_port())
    monkeypatch.setattr(svc, "_port_open", lambda *a, **k: False)
    svc._running_cache.update({"ts": 9e9, "value": True, "key": "x"})
    svc._proc = _fake_proc(alive=False)
    assert svc.is_running(str(tmp_path)) is False


def test_managed_process_alive_tracks_the_child_handle() -> None:
    assert svc.managed_process_alive() is False
    svc._proc = _fake_proc(alive=True)
    assert svc.managed_process_alive() is True
    svc._proc = _fake_proc(alive=False)
    assert svc.managed_process_alive() is False


def test_is_starting_stays_true_for_the_whole_spawn_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    assert svc.is_starting() is False
    svc._mark_starting(60.0)
    assert svc.is_starting() is True
    svc._clear_starting()
    assert svc.is_starting() is False


def test_mark_starting_has_a_floor_so_a_zero_window_is_not_instantly_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    svc._mark_starting(0.0)
    assert svc.is_starting() is True


def test_a_closed_port_is_not_loading(tmp_path: Path) -> None:
    _seed(tmp_path, port=_closed_port())
    assert svc.is_loading(str(tmp_path)) is False
    assert svc.loading_for_s(str(tmp_path)) == 0.0
    assert svc.loading_stalled(str(tmp_path)) is False


def test_an_open_but_unhealthy_port_is_loading_and_starts_the_clock(
    tmp_path: Path, fake_http: Any
) -> None:
    _seed(tmp_path, host=fake_http.host, port=fake_http.port)
    fake_http.route("*", status=503, json={"error": "Loading model"})
    assert svc.is_loading(str(tmp_path)) is True
    assert svc.loading_for_s(str(tmp_path)) > 0
    # Freshly loading is not stalled — the stall floor is 30s whatever is asked.
    assert svc.loading_stalled(str(tmp_path), max_s=0.0) is False


def test_a_healthy_host_clears_the_loading_clock(tmp_path: Path, fake_http: Any) -> None:
    _seed(tmp_path, host=fake_http.host, port=fake_http.port)
    svc._note_loading_state(True)
    fake_http.route("/health", json={"status": "ok"})
    assert svc.is_loading(str(tmp_path)) is False
    assert svc._loading_since == 0.0


# --------------------------------------------------------------------------
# process discovery / killing — the code that can destroy the wrong thing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pid", [0, -1, -9999])
def test_kill_pid_refuses_a_non_positive_pid(pid: int, monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(*a: object, **k: object) -> None:
        raise AssertionError("must not shell out for a bogus pid")

    monkeypatch.setattr(subprocess, "run", _explode)
    assert _REAL_KILL_PID(pid) is False


@pytest.mark.parametrize("pid", [0, -1])
def test_looks_like_llama_server_refuses_a_non_positive_pid(
    pid: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode(*a: object, **k: object) -> None:
        raise AssertionError("must not inspect a bogus pid")

    monkeypatch.setattr(subprocess, "run", _explode)
    assert _REAL_LOOKS_LIKE_LLAMA(pid) is False


@_needs_windows
@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("", False),  # process is gone — nothing to kill
        ("notepad C:\\Windows\\notepad.exe", False),
        ("llama-server C:\\rmb\\llama-server.exe", True),
        ("llama_server /opt/llama_server", True),
    ],
)
def test_looks_like_llama_server_judges_by_process_name(
    stdout: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout))
    assert _REAL_LOOKS_LIKE_LLAMA(1234) is expected


@_needs_windows
def test_when_the_process_cannot_be_inspected_the_caller_decides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sticky host is worse than a false positive, so failure means "maybe"."""

    def _explode(*a: object, **k: object) -> None:
        raise OSError("powershell missing")

    monkeypatch.setattr(subprocess, "run", _explode)
    assert _REAL_LOOKS_LIKE_LLAMA(1234) is True


@pytest.mark.parametrize("port", [0, -1])
def test_find_pid_on_port_refuses_an_invalid_port(
    port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode(*a: object, **k: object) -> None:
        raise AssertionError("must not shell out for an invalid port")

    monkeypatch.setattr(subprocess, "run", _explode)
    assert _REAL_FIND_PID(port) is None


@_needs_windows
def test_find_pid_on_port_only_reads_listening_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    netstat = "\n".join(
        [
            "  Proto  Local Address    Foreign Address    State    PID",
            "  TCP    127.0.0.1:8787   127.0.0.1:5501     ESTABLISHED    111",
            "  TCP    127.0.0.1:9999   0.0.0.0:0          LISTENING      222",
            "  TCP    127.0.0.1:8787   0.0.0.0:0          LISTENING      333",
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(netstat))
    assert _REAL_FIND_PID(8787) == 333


@_needs_windows
def test_find_pid_on_port_reports_nothing_when_the_tool_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(*a: object, **k: object) -> None:
        raise OSError("netstat missing")

    monkeypatch.setattr(subprocess, "run", _explode)
    assert _REAL_FIND_PID(8787) is None


@_needs_windows
def test_freeing_a_port_never_kills_the_remedy_process_itself(
    monkeypatch: pytest.MonkeyPatch, rmb_guard: _Guard
) -> None:
    """os.getpid() on the port means Remedy bound it — killing it kills Remedy."""
    netstat = "\n".join(
        [
            f"  TCP    127.0.0.1:8787   0.0.0.0:0   LISTENING   {os.getpid()}",
            "  TCP    127.0.0.1:8787   0.0.0.0:0   LISTENING   424242",
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(netstat))
    killed = _REAL_KILL_LISTENERS(8787)
    assert os.getpid() not in rmb_guard.killed_pids
    assert rmb_guard.killed_pids == [424242]
    assert killed == 1


@pytest.mark.parametrize("port", [0, -1])
def test_freeing_an_invalid_port_is_a_noop(port: int, monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(*a: object, **k: object) -> None:
        raise AssertionError("must not shell out for an invalid port")

    monkeypatch.setattr(subprocess, "run", _explode)
    assert _REAL_KILL_LISTENERS(port) == 0


# --------------------------------------------------------------------------
# log tail — feeds the error payload the user actually reads
# --------------------------------------------------------------------------


def test_a_missing_log_is_reported_as_empty_not_raised(tmp_path: Path) -> None:
    assert svc._tail_log(None) == ""
    assert svc._tail_log(tmp_path / "nope.log") == ""
    assert svc._tail_log(tmp_path) == ""  # a directory, not a file


def test_the_log_tail_is_capped_and_keeps_the_end(tmp_path: Path) -> None:
    log = tmp_path / "llama-server.log"
    log.write_bytes(b"A" * 5000 + b"CUDA out of memory")
    tail = svc._tail_log(log, max_bytes=64)
    assert len(tail) <= 64
    assert tail.endswith("CUDA out of memory")


def test_undecodable_log_bytes_do_not_break_the_error_path(tmp_path: Path) -> None:
    log = tmp_path / "llama-server.log"
    log.write_bytes(b"\xff\xfe\x00bad utf8")
    assert "bad utf8" in svc._tail_log(log)


# --------------------------------------------------------------------------
# model discovery
# --------------------------------------------------------------------------


def test_extra_model_dirs_from_env_are_used_and_missing_ones_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "extra"
    real.mkdir()
    ghost = tmp_path / "ghost"
    monkeypatch.setenv("REMEDY_RMB_MODEL_DIRS", f"{real}{os.pathsep}{ghost}")
    roots = svc._model_search_roots(str(tmp_path))
    assert real in roots
    assert ghost not in roots
    # De-duped: the same directory must not be scanned twice.
    keys = [str(r).lower() for r in roots]
    assert len(keys) == len(set(keys))


def test_old_muscle_bridge_folder_is_after_house_models(tmp_path: Path) -> None:
    """Live session treated ~/Remedy Muscle Bridge as the house over ~/.remedy/rmb."""
    names = [str(r) for r in svc._model_search_roots(str(tmp_path))]
    old = [i for i, n in enumerate(names) if "Remedy Muscle Bridge" in n]
    house = [
        i
        for i, n in enumerate(names)
        if "rmb" in n.replace("\\", "/").lower() and n.lower().rstrip("\\/").endswith("models")
    ]
    assert old, names
    assert house, names
    assert min(old) > max(house)


def test_discover_ggufs_includes_the_configured_path_outside_every_search_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stray = tmp_path / "elsewhere" / "Qwopus3.5-9B-Coder-MTP-Q4_K_M.gguf"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"gguf")
    _seed(tmp_path, model_path=str(stray))
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [models_dir(str(tmp_path))])
    names = [g["name"] for g in svc.discover_ggufs(str(tmp_path))]
    assert stray.name in names


def test_discover_ggufs_ignores_non_gguf_files_and_sorts_biggest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = models_dir(str(tmp_path))
    (root / "notes.txt").write_bytes(b"x" * 10)
    (root / "small-7B-Q4_K_M.gguf").write_bytes(b"x" * 10)
    (root / "big-14B-Q4_K_M.gguf").write_bytes(b"x" * 5000)
    _seed(tmp_path)
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [root])
    found = svc.discover_ggufs(str(tmp_path))
    names = [g["name"] for g in found]
    assert "notes.txt" not in names
    assert names == ["big-14B-Q4_K_M.gguf", "small-7B-Q4_K_M.gguf"]
    assert found[0]["id"] == "big-14B-Q4_K_M"


def test_discover_ggufs_serves_a_cached_list_within_its_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = models_dir(str(tmp_path))
    (root / "first-7B-Q4_K_M.gguf").write_bytes(b"x")
    _seed(tmp_path)
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [root])
    first = svc.discover_ggufs(str(tmp_path))
    (root / "second-7B-Q4_K_M.gguf").write_bytes(b"x")
    assert svc.discover_ggufs(str(tmp_path)) == first
    svc._discover_ggufs_cache.update({"ts": 0.0, "key": "", "value": []})
    assert len(svc.discover_ggufs(str(tmp_path))) == 2


def test_the_cached_list_is_a_copy_so_a_caller_cannot_poison_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = models_dir(str(tmp_path))
    (root / "only-7B-Q4_K_M.gguf").write_bytes(b"x")
    _seed(tmp_path)
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [root])
    assert len(svc.discover_ggufs(str(tmp_path))) == 1
    # Second call is served from the cache — clearing it must not empty the cache.
    svc.discover_ggufs(str(tmp_path)).clear()
    assert len(svc.discover_ggufs(str(tmp_path))) == 1


def test_a_sticky_path_the_user_picked_is_never_swapped_for_a_catalog_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = models_dir(str(tmp_path))
    catalog = root / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    catalog.write_bytes(b"gguf")
    picked = tmp_path / "Downloads" / "Qwopus3.5-9B-Coder-MTP-Q4_K_M.gguf"
    picked.parent.mkdir(parents=True)
    picked.write_bytes(b"gguf")
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [root])
    state = merge_state({"model_id": "qwen25-coder-7b", "model_path": str(picked)})
    trusted = svc._resolve_model_path(state, str(tmp_path), trust_sticky_path=True)
    assert trusted == picked
    # Without the trust flag the 7B catalog id wins over a mismatched sticky path.
    untrusted = svc._resolve_model_path(state, str(tmp_path), trust_sticky_path=False)
    assert untrusted == catalog


def test_a_configured_path_that_no_longer_exists_falls_back_to_a_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = models_dir(str(tmp_path))
    catalog = root / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    catalog.write_bytes(b"gguf")
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [root])
    state = merge_state(
        {"model_id": "qwen25-coder-7b", "model_path": str(tmp_path / "deleted.gguf")}
    )
    assert svc._resolve_model_path(state, str(tmp_path), trust_sticky_path=True) == catalog


def test_no_gguf_anywhere_resolves_to_nothing_rather_than_a_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [models_dir(str(tmp_path))])
    state = merge_state({"model_id": "qwen25-coder-14b"})
    assert svc._resolve_model_path(state, str(tmp_path)) is None


# --------------------------------------------------------------------------
# runtime binary discovery
# --------------------------------------------------------------------------


@pytest.fixture()
def no_installed_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the binary search to whatever is really installed on this host."""
    import remedy.runtime.bundle as bundle
    import remedy.runtime.catalog as rt_catalog
    import remedy.vision.install as vision_install

    monkeypatch.setattr(vision_install, "runtime_binary_path", lambda *a, **k: None)
    monkeypatch.setattr(bundle, "runtime_binary_from_bundle", lambda *a, **k: None)
    monkeypatch.setattr(rt_catalog, "host_runtime_ids", lambda *a, **k: [])
    monkeypatch.delenv("REMEDY_LLAMA_SERVER", raising=False)


def test_a_configured_runtime_binary_wins_over_every_fallback(
    tmp_path: Path, no_installed_runtime: None
) -> None:
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"stub")
    found = svc._find_llama_binary({"runtime_binary": str(binary)}, str(tmp_path))
    assert found == binary


def test_a_configured_runtime_binary_that_is_gone_is_not_returned(
    tmp_path: Path, no_installed_runtime: None
) -> None:
    state = {"runtime_binary": str(tmp_path / "missing.exe")}
    assert svc._find_llama_binary(state, str(tmp_path)) is None


def test_the_env_override_is_the_last_resort_and_must_point_at_a_real_file(
    tmp_path: Path, no_installed_runtime: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_LLAMA_SERVER", str(tmp_path / "ghost.exe"))
    assert svc._find_llama_binary({}, str(tmp_path)) is None
    binary = tmp_path / "dev-llama-server.exe"
    binary.write_bytes(b"stub")
    monkeypatch.setenv("REMEDY_LLAMA_SERVER", str(binary))
    assert svc._find_llama_binary({}, str(tmp_path)) == binary


# --------------------------------------------------------------------------
# binary capability probes — wrong answers here mean a host that will not start
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "probe",
    [
        svc.binary_supports_draft_mtp,
        svc.binary_supports_cache_reuse,
        svc.binary_supports_chat_template_kwargs,
        svc.binary_supports_reasoning_budget,
    ],
)
def test_capability_probes_refuse_a_missing_binary(probe: Any, tmp_path: Path) -> None:
    assert probe(None) is False
    assert probe("") is False
    assert probe(tmp_path / "not-here.exe") is False
    assert probe(tmp_path) is False  # a directory is not a binary


@pytest.mark.parametrize(
    ("probe", "needle"),
    [
        (svc.binary_supports_cache_reuse, b"--cache-reuse"),
        (svc.binary_supports_chat_template_kwargs, b"--chat-template-kwargs"),
        (svc.binary_supports_reasoning_budget, b"--reasoning-budget"),
    ],
)
def test_capability_flags_are_found_in_a_sibling_dll(
    probe: Any, needle: bytes, tmp_path: Path
) -> None:
    """Windows CUDA builds keep the flag strings in llama-common.dll, not the exe."""
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"no flags here")
    assert probe(binary) is False
    svc._flag_cap_cache.clear()
    (tmp_path / "llama-common.dll").write_bytes(b"pad" + needle + b"pad")
    assert probe(binary) is True


def test_a_capability_probe_skips_files_too_large_to_be_the_flag_carrier(
    tmp_path: Path,
) -> None:
    """ggml-cuda.dll is hundreds of MB; reading it on every Start is not ok."""
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"stub")
    huge = tmp_path / "llama-common.dll"
    with open(huge, "wb") as fh:
        fh.write(b"--cache-reuse")
        fh.truncate(41 * 1024 * 1024)
    assert svc.binary_supports_cache_reuse(binary) is False


def test_a_capability_answer_is_cached_until_the_binary_changes(tmp_path: Path) -> None:
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"stub")
    assert svc.binary_supports_draft_mtp(binary) is False
    stat = binary.stat()
    binary.write_bytes(b"--spec-type draft-mtp")
    os.utime(binary, (stat.st_atime, stat.st_mtime))  # same mtime → cache still valid
    assert svc.binary_supports_draft_mtp(binary) is False
    os.utime(binary, (stat.st_atime, stat.st_mtime + 10))
    assert svc.binary_supports_draft_mtp(binary) is True


# --------------------------------------------------------------------------
# argv construction
# --------------------------------------------------------------------------


def _cmd(tmp_path: Path, **kwargs: Any) -> list[str]:
    binary = tmp_path / "llama-server.exe"
    model = tmp_path / "model-7B-Q4_K_M.gguf"
    base: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 8787,
        "ctx": 8192,
        "ngl": -1,
        "threads": 0,
        "parallel": 1,
        "flash_attn": False,
        "host_profile": {},
    }
    base.update(kwargs)
    return svc._build_cmd(binary, model, **base)


def test_context_and_slot_count_have_floors_llama_server_will_accept(tmp_path: Path) -> None:
    cmd = _cmd(tmp_path, ctx=16, parallel=0)
    assert cmd[cmd.index("--ctx-size") + 1] == "2048"
    assert cmd[cmd.index("--parallel") + 1] == "1"


@pytest.mark.parametrize(
    ("kwargs", "flag"),
    [
        ({"seed": -1}, "--seed"),
        ({"top_k": 0}, "--top-k"),
        ({"top_p": 0.0}, "--top-p"),
        ({"top_p": 1.5}, "--top-p"),
        ({"min_p": 1.0}, "--min-p"),
        ({"temperature": -1.0}, "--temp"),
        ({"repeat_penalty": 0.0}, "--repeat-penalty"),
        ({"threads": 0}, "--threads"),
        ({"batch_size": 0}, "--batch-size"),
        ({"rope_freq_base": 0.0}, "--rope-freq-base"),
        ({"typical_p": 0.0}, "--typical"),
        ({"tfs_z": 0.0}, "--tfs"),
        ({"xtc_probability": 0.0}, "--xtc-probability"),
        ({"dry_multiplier": 0.0}, "--dry-multiplier"),
        ({"chat_template": "   "}, "--chat-template"),
        ({"mmproj": ""}, "--mmproj"),
        ({"cache_type": "  "}, "--cache-type-k"),
    ],
)
def test_an_off_or_out_of_range_knob_is_left_off_the_command_line(
    tmp_path: Path, kwargs: dict[str, Any], flag: str
) -> None:
    """llama-server rejects nonsense values — the default must be *absence*."""
    assert flag not in _cmd(tmp_path, **kwargs)


@pytest.mark.parametrize("value", ["", "  ", "bogus", "LINEARISH"])
def test_an_unknown_rope_scaling_mode_is_refused(tmp_path: Path, value: str) -> None:
    cmd = _cmd(tmp_path, rope_scaling=value, yarn_factor=2.0)
    assert "--rope-scaling" not in cmd
    assert "--yarn-factor" not in cmd


def test_yarn_knobs_only_ship_alongside_a_rope_scaling_mode(tmp_path: Path) -> None:
    cmd = _cmd(tmp_path, rope_scaling="YaRN", yarn_factor=2.0, yarn_orig_ctx=4096)
    assert cmd[cmd.index("--rope-scaling") + 1] == "yarn"
    assert cmd[cmd.index("--yarn-factor") + 1] == "2.0"
    assert cmd[cmd.index("--yarn-orig-ctx") + 1] == "4096"


def test_mirostat_tuning_is_dropped_when_mirostat_is_off(tmp_path: Path) -> None:
    cmd = _cmd(tmp_path, mirostat=0, mirostat_tau=5.0, mirostat_eta=0.1)
    assert cmd[cmd.index("--mirostat") + 1] == "0"
    assert "--mirostat-tau" not in cmd
    assert "--mirostat-eta" not in cmd


def test_dry_tuning_is_dropped_when_dry_sampling_is_off(tmp_path: Path) -> None:
    cmd = _cmd(tmp_path, dry_multiplier=0.0, dry_base=1.75, dry_allowed_length=2)
    assert "--dry-base" not in cmd
    assert "--dry-allowed-length" not in cmd


def test_a_profile_that_demands_one_slot_overrides_the_configured_parallelism(
    tmp_path: Path,
) -> None:
    cmd = _cmd(tmp_path, parallel=8, host_profile={"force_parallel_1": True})
    assert cmd[cmd.index("--parallel") + 1] == "1"


def test_cache_reuse_is_not_passed_to_a_binary_that_does_not_know_it(tmp_path: Path) -> None:
    assert "--cache-reuse" not in _cmd(tmp_path, cache_reuse=256)


# --------------------------------------------------------------------------
# engine knob extraction
# --------------------------------------------------------------------------


def test_unset_blank_and_uncastable_knobs_never_reach_the_command_line() -> None:
    out = svc._engine_kwargs(
        {
            "temperature": None,
            "top_k": "",
            "top_p": "   ",
            "seed": "not-a-number",
            "batch_size": [1, 2],
            "mmproj": "   ",
            "cache_type": "",
            "min_p": 0.05,
        }
    )
    for absent in ("temperature", "top_k", "top_p", "seed", "batch_size", "mmproj", "cache_type"):
        assert absent not in out
    assert out["min_p"] == 0.05


def test_boolean_toggles_are_always_reported_so_defaults_are_explicit() -> None:
    out = svc._engine_kwargs({})
    assert out["use_jinja"] is True
    assert out["mlock"] is False
    assert out["no_mmap"] is False
    assert out["no_kv_offload"] is False


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("ctx_size", "8192", 8192),
        ("port", 8787.0, 8787),
        ("ctx_size", "not-a-number", "not-a-number"),
        ("temperature", "0.8", 0.8),
        ("temperature", "warm", "warm"),
        ("flash_attn", 1, True),
        ("flash_attn", None, False),
        ("model_path", "  C:\\m.gguf  ", "C:\\m.gguf"),
        ("model_path", None, ""),
    ],
)
def test_process_diff_normalisation_never_raises_on_junk(
    key: str, raw: Any, expected: Any
) -> None:
    """A junk value must compare equal to itself, not blow up the settings save."""
    assert svc._norm_rmb_val(key, raw) == expected


# --------------------------------------------------------------------------
# lifecycle refusals
# --------------------------------------------------------------------------


def test_a_persisted_user_stop_survives_an_api_recycle(tmp_path: Path) -> None:
    _seed(tmp_path, user_stopped=True)
    svc._user_stopped = False  # fresh process, in-memory bit lost
    assert svc._refresh_user_stopped(str(tmp_path)) is True
    assert svc._user_stopped is True


def test_persisting_the_stay_off_bit_writes_it_to_disk(tmp_path: Path) -> None:
    _seed(tmp_path)
    svc._persist_user_stopped(str(tmp_path), True)
    assert load_rmb_json(str(tmp_path))["user_stopped"] is True
    svc._persist_user_stopped(str(tmp_path), False)
    assert load_rmb_json(str(tmp_path))["user_stopped"] is False
    assert svc._user_stopped is False


def test_start_is_refused_while_the_user_has_it_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, user_stopped=True)
    monkeypatch.setattr(
        svc,
        "_start_rmb_server_impl",
        lambda **k: pytest.fail("must not spawn while user-stopped"),
    )
    result = svc.start_rmb_server(home_dir=str(tmp_path))
    assert result == {"ok": False, "error": "RMB stopped by user"}


def test_an_explicit_start_clears_the_stay_off_bit_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, user_stopped=True)
    monkeypatch.setattr(svc, "_start_rmb_server_impl", lambda **k: {"ok": True})
    svc.start_rmb_server(home_dir=str(tmp_path), clear_user_stopped=True)
    assert load_rmb_json(str(tmp_path))["user_stopped"] is False


def test_a_crashing_start_is_reported_and_releases_the_single_flight_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)

    def _boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(svc, "_start_rmb_server_impl", _boom)
    result = svc.start_rmb_server(home_dir=str(tmp_path))
    assert result["ok"] is False
    assert "driver exploded" in result["error"]
    assert svc._start_flight_active is False
    assert svc._last_start_error == result["error"]


def test_a_failed_start_is_remembered_for_the_watchdog_and_the_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(
        svc, "_start_rmb_server_impl", lambda **k: {"ok": False, "error": "no GGUF"}
    )
    assert svc.start_rmb_server(home_dir=str(tmp_path))["error"] == "no GGUF"
    assert svc._last_start_error == "no GGUF"
    monkeypatch.setattr(svc, "_start_rmb_server_impl", lambda **k: {"ok": True})
    svc.start_rmb_server(home_dir=str(tmp_path))
    assert svc._last_start_error is None


def test_a_second_caller_joins_the_in_flight_start_instead_of_racing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(
        svc,
        "_start_rmb_server_impl",
        lambda **k: pytest.fail("joiner must not spawn a second host"),
    )
    svc._start_flight_active = True
    svc._start_flight_result = {"ok": False, "error": "leader failed"}
    svc._start_flight_event.set()
    joined = svc.start_rmb_server(home_dir=str(tmp_path), wait_s=0.0)
    assert joined["error"] == "leader failed"
    # A copy — a joiner mutating its result must not corrupt the leader's.
    joined["error"] = "tampered"
    assert svc._start_flight_result["error"] == "leader failed"


def test_start_reports_a_missing_runtime_binary_and_unsuspends_vision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, enabled=True, port=_closed_port())
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(svc, "_suspend_smolvlm", lambda home=None: {"stopped": False})
    monkeypatch.setattr(svc, "_find_llama_binary", lambda state, home: None)
    result = svc._start_rmb_server_impl(home_dir=str(tmp_path), wait_s=1.0)
    assert result["ok"] is False
    assert "llama-server binary not found" in result["error"]
    assert result["vision_suspended"] is False
    assert load_rmb_json(str(tmp_path))["vision_suspended"] is False


def test_start_reports_a_missing_gguf_with_the_folder_to_drop_it_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"stub")
    _seed(tmp_path, enabled=True, port=_closed_port(), runtime_binary=str(binary))
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(svc, "_suspend_smolvlm", lambda home=None: {"stopped": False})
    monkeypatch.setattr(svc, "_resolve_model_path", lambda *a, **k: None)
    monkeypatch.setattr(svc, "discover_ggufs", lambda home=None: [])
    result = svc._start_rmb_server_impl(home_dir=str(tmp_path), wait_s=1.0)
    assert result["ok"] is False
    assert "GGUF not found" in result["error"]
    assert str(models_dir(str(tmp_path))) in result["error"]
    assert result["vision_suspended"] is False


def test_stopping_by_user_intent_makes_the_stay_off_bit_stick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rmb_guard: _Guard
) -> None:
    port = _closed_port()
    _seed(tmp_path, enabled=True, port=port, pid=99999, vision_suspended=True)
    monkeypatch.setattr(svc, "_port_open", lambda *a, **k: False)
    result = svc.stop_rmb_server(str(tmp_path), resume_vision=False, user_intent=True)
    assert result["ok"] is True
    on_disk = load_rmb_json(str(tmp_path))
    assert on_disk["user_stopped"] is True
    assert on_disk["pid"] is None
    assert on_disk["vision_suspended"] is False
    assert svc._user_stopped is True
    assert 99999 in rmb_guard.killed_pids
    assert port in rmb_guard.killed_ports


def test_stopping_to_swap_models_must_not_leave_the_host_stuck_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, enabled=True, port=_closed_port())
    monkeypatch.setattr(svc, "_port_open", lambda *a, **k: False)
    svc.stop_rmb_server(str(tmp_path), resume_vision=False, user_intent=False)
    assert load_rmb_json(str(tmp_path)).get("user_stopped") is False
    assert svc._user_stopped is False


def test_a_garbage_pid_in_rmb_json_is_not_turned_into_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rmb_guard: _Guard
) -> None:
    _seed(tmp_path, port=_closed_port(), pid="not-a-pid")
    monkeypatch.setattr(svc, "_port_open", lambda *a, **k: False)
    svc.stop_rmb_server(str(tmp_path), resume_vision=False, user_intent=False)
    assert rmb_guard.killed_pids == []


def test_stop_reports_nothing_stopped_when_nothing_was_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, port=_closed_port(), pid=None)
    monkeypatch.setattr(svc, "_port_open", lambda *a, **k: False)
    result = svc.stop_rmb_server(str(tmp_path), resume_vision=False, user_intent=False)
    assert result["stopped"] is False
    assert result["vision_resume"] == {"resumed": False, "skipped": True}


def test_ensure_refuses_a_disabled_host_but_force_overrides_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, enabled=False, port=_closed_port())
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "adopt_existing_host", lambda home=None: {"ok": False})
    starts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc, "start_rmb_server", lambda **k: starts.append(k) or {"ok": True}
    )
    assert svc.ensure_rmb_server(str(tmp_path)) == {"ok": False, "error": "RMB not enabled"}
    assert starts == []
    assert svc.ensure_rmb_server(str(tmp_path), force=True)["ok"] is True
    assert starts and starts[0]["wait_s"] == 90.0


def test_ensure_refuses_after_a_user_stop_unless_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, enabled=True, user_stopped=True, port=_closed_port())
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "adopt_existing_host", lambda home=None: {"ok": False})
    monkeypatch.setattr(
        svc, "start_rmb_server", lambda **k: pytest.fail("must not start after user stop")
    )
    assert svc.ensure_rmb_server(str(tmp_path)) == {"ok": False, "error": "RMB stopped by user"}


def test_waking_in_the_background_is_refused_after_a_user_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, user_stopped=True, port=_closed_port())
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "is_loading", lambda *a, **k: False)
    monkeypatch.setattr(svc, "adopt_existing_host", lambda home=None: {"ok": False})
    monkeypatch.setattr(
        svc, "start_rmb_server", lambda **k: pytest.fail("must not wake after user stop")
    )
    result = svc.wake_rmb_async(str(tmp_path))
    assert result["ok"] is False
    assert "stopped by user" in result["error"]


def test_waking_adopts_an_orphan_host_instead_of_spawning_a_second_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, port=_closed_port())
    monkeypatch.setattr(
        svc, "adopt_existing_host", lambda home=None: {"ok": True, "adopted": True, "pid": 7}
    )
    monkeypatch.setattr(
        svc, "start_rmb_server", lambda **k: pytest.fail("must not spawn beside an adopted host")
    )
    result = svc.wake_rmb_async(str(tmp_path))
    assert result == {"ok": True, "already_running": True, "adopted": True}


def test_adopting_refuses_a_host_that_is_not_healthy(
    tmp_path: Path, fake_http: Any
) -> None:
    _seed(tmp_path, host=fake_http.host, port=fake_http.port)
    fake_http.route("*", status=503, json={"error": "Loading model"})
    result = svc.adopt_existing_host(str(tmp_path))
    assert result == {"ok": False, "adopted": False, "reason": "not_healthy"}
    assert load_rmb_json(str(tmp_path)).get("pid") is None


def test_adopting_records_the_pid_and_marks_vision_suspended(
    tmp_path: Path, fake_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, host=fake_http.host, port=fake_http.port)
    fake_http.route("/health", json={"status": "ok"})
    monkeypatch.setattr(svc, "_find_pid_on_port", lambda port: 5150)
    result = svc.adopt_existing_host(str(tmp_path))
    assert result["adopted"] is True
    on_disk = load_rmb_json(str(tmp_path))
    assert on_disk["pid"] == 5150
    assert on_disk["vision_suspended"] is True


def test_waiting_for_ready_gives_up_immediately_when_the_user_stopped_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, user_stopped=True, port=_closed_port())
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(
        svc, "start_rmb_server", lambda **k: pytest.fail("must not start after user stop")
    )
    result = svc.wait_rmb_ready(str(tmp_path), timeout_s=5.0)
    assert result == {"ok": False, "ready": False, "error": "RMB was stopped by user"}


def test_waiting_for_ready_returns_at_once_when_the_host_is_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, port=_closed_port())
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: True)
    assert svc.wait_rmb_ready(str(tmp_path), timeout_s=5.0) == {"ok": True, "ready": True}


def test_resuming_vision_is_refused_while_rmb_still_owns_the_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SmolVLM must never be woken back onto a GPU RMB is still loading onto."""
    import remedy.interfaces.api_support as api_support
    import remedy.runtime.rmb.mode as rmb_mode
    import remedy.vision.service as vision_service

    monkeypatch.setattr(api_support, "load_config", lambda: {})
    monkeypatch.setattr(rmb_mode, "is_local_agent_mode", lambda *a, **k: False)
    monkeypatch.setattr(
        vision_service, "ensure_server", lambda cfg=None: pytest.fail("RMB still owns the GPU")
    )
    monkeypatch.setattr(svc, "is_starting", lambda: True)
    result = svc._resume_smolvlm_if_wanted(None)
    assert result == {"resumed": False, "reason": "rmb_still_running"}


def test_resuming_vision_is_refused_when_chat_is_still_the_local_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import remedy.interfaces.api_support as api_support
    import remedy.runtime.rmb.mode as rmb_mode
    import remedy.vision.service as vision_service

    monkeypatch.setattr(api_support, "load_config", lambda: {})
    monkeypatch.setattr(rmb_mode, "is_local_agent_mode", lambda *a, **k: True)
    monkeypatch.setattr(
        vision_service, "ensure_server", lambda cfg=None: pytest.fail("chat is still RMB")
    )
    assert svc._resume_smolvlm_if_wanted(None) == {
        "resumed": False,
        "reason": "chat_still_rmb",
    }


# --------------------------------------------------------------------------
# settings + chat identity
# --------------------------------------------------------------------------


def test_a_context_window_below_the_floor_is_not_pushed_into_the_budget_cache() -> None:
    assert svc.sync_context_window_cache({"ctx_size": 512}) == 0
    assert svc.sync_context_window_cache({"ctx_size": "nonsense"}) == 0
    assert svc.sync_context_window_cache({}) == 0


def test_switching_chat_model_refuses_an_empty_hint() -> None:
    assert svc.apply_rmb_chat_model("") == {"ok": False, "error": "empty model"}
    assert svc.apply_rmb_chat_model(None) == {"ok": False, "error": "empty model"}
    assert svc.apply_rmb_chat_model("   ") == {"ok": False, "error": "empty model"}


def test_chat_identity_sync_does_nothing_without_a_loaded_gguf() -> None:
    assert svc.sync_rmb_chat_identity({}) == {"synced": False}
    assert svc.sync_rmb_chat_identity({"model_path": "   "}) == {"synced": False}


@pytest.fixture()
def offline_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """apply_rmb_settings without touching the process or config.toml."""
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "managed_process_alive", lambda: False)
    monkeypatch.setattr(svc, "sync_rmb_chat_identity", lambda *a, **k: {"synced": False})
    monkeypatch.setattr(svc, "get_rmb_status", lambda cfg=None: {"ok": True})
    monkeypatch.setattr(
        svc, "start_rmb_server", lambda **k: pytest.fail("live=False must not start")
    )


@pytest.mark.parametrize(
    ("patch", "key", "expected"),
    [
        ({"ctx_size": 10}, "ctx_size", 2048),
        ({"ctx_size": 99_999_999}, "ctx_size", 1_048_576),
        ({"port": 0}, "port", 1),
        ({"port": 70000}, "port", 65535),
        ({"mirostat": 9}, "mirostat", 2),
        ({"mirostat": -4}, "mirostat", 0),
        ({"dry_penalty_last_n": -50}, "dry_penalty_last_n", -1),
        ({"dry_penalty_last_n": 10**9}, "dry_penalty_last_n", 65536),
    ],
)
def test_out_of_range_settings_are_clamped_not_written_through(
    tmp_path: Path,
    offline_settings: None,
    patch: dict[str, Any],
    key: str,
    expected: Any,
) -> None:
    _seed(tmp_path, enabled=True)
    svc.apply_rmb_settings(patch, home_dir=str(tmp_path), cfg={}, live=False)
    assert load_rmb_json(str(tmp_path))[key] == expected


@pytest.mark.parametrize(
    ("key", "junk"),
    [
        ("ctx_size", "wide"),
        ("port", "eighty-seven"),
        ("n_gpu_layers", "all"),
        ("top_k", "many"),
        ("temperature", "warm"),
        ("mirostat", "v2"),
    ],
)
def test_an_uncastable_setting_leaves_the_previous_value_alone(
    tmp_path: Path, offline_settings: None, key: str, junk: str
) -> None:
    before = _seed(tmp_path, enabled=True)
    svc.apply_rmb_settings({key: junk}, home_dir=str(tmp_path), cfg={}, live=False)
    assert load_rmb_json(str(tmp_path))[key] == before[key]


def test_keys_that_are_not_settings_are_ignored_entirely(
    tmp_path: Path, offline_settings: None
) -> None:
    _seed(tmp_path, enabled=True)
    svc.apply_rmb_settings(
        {"rm_rf": "/", "llm_api_key": "secret", "pid": 1234},
        home_dir=str(tmp_path),
        cfg={},
        live=False,
    )
    on_disk = load_rmb_json(str(tmp_path))
    assert "rm_rf" not in on_disk
    assert "llm_api_key" not in on_disk
    assert on_disk["pid"] is None


def test_choosing_a_fixed_profile_locks_autofit_out(
    tmp_path: Path, offline_settings: None
) -> None:
    _seed(tmp_path, enabled=True, autofit=True, autofit_locked=False)
    svc.apply_rmb_settings({"profile": "quality"}, home_dir=str(tmp_path), cfg={}, live=False)
    on_disk = load_rmb_json(str(tmp_path))
    assert on_disk["autofit"] is False
    assert on_disk["autofit_locked"] is True
    assert on_disk["ctx_size"] == 16384


def test_editing_the_context_window_by_hand_locks_autofit_out(
    tmp_path: Path, offline_settings: None
) -> None:
    _seed(tmp_path, enabled=True, autofit=True, autofit_locked=False)
    svc.apply_rmb_settings({"ctx_size": 4096}, home_dir=str(tmp_path), cfg={}, live=False)
    assert load_rmb_json(str(tmp_path))["autofit_locked"] is True


def test_returning_to_autofit_forgets_the_last_good_fit_so_it_can_retry_bigger(
    tmp_path: Path, offline_settings: None
) -> None:
    _seed(tmp_path, enabled=True, last_good_fit={"ctx_size": 4096}, autofit_locked=True)
    svc.apply_rmb_settings({"profile": "autofit"}, home_dir=str(tmp_path), cfg={}, live=False)
    on_disk = load_rmb_json(str(tmp_path))
    assert on_disk["autofit"] is True
    assert on_disk["autofit_locked"] is False
    assert on_disk["last_good_fit"] is None


def test_an_unknown_profile_is_ignored_rather_than_stored_as_a_profile(
    tmp_path: Path, offline_settings: None
) -> None:
    _seed(tmp_path, enabled=True, autofit=True, autofit_locked=False)
    svc.apply_rmb_settings({"profile": "ludicrous"}, home_dir=str(tmp_path), cfg={}, live=False)
    on_disk = load_rmb_json(str(tmp_path))
    assert on_disk["autofit"] is True
    assert on_disk["autofit_locked"] is False


def test_base_url_always_follows_host_and_port(tmp_path: Path, offline_settings: None) -> None:
    _seed(tmp_path, enabled=True)
    svc.apply_rmb_settings(
        {"host": "127.0.0.1", "port": 8899, "base_url": "http://evil.example/v1"},
        home_dir=str(tmp_path),
        cfg={},
        live=False,
    )
    assert load_rmb_json(str(tmp_path))["base_url"] == "http://127.0.0.1:8899/v1"


def test_a_settings_patch_does_not_steal_the_chat_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ``use_as_chat_provider`` may point chat at RMB — never a ctx edit."""
    _seed(tmp_path, enabled=True)
    seen: list[bool] = []
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "managed_process_alive", lambda: False)
    monkeypatch.setattr(svc, "get_rmb_status", lambda cfg=None: {"ok": True})
    monkeypatch.setattr(
        svc,
        "sync_rmb_chat_identity",
        lambda state, home_dir=None, force_provider=False: seen.append(force_provider) or {},
    )
    svc.apply_rmb_settings({"ctx_size": 4096}, home_dir=str(tmp_path), cfg={}, live=False)
    assert seen == [False]


# --------------------------------------------------------------------------
# status payload
# --------------------------------------------------------------------------


@pytest.fixture()
def offline_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_rmb_status without GPU probes, disk scans or auto-heal spawns."""
    monkeypatch.setattr(svc, "_nvidia_ok", lambda: False)
    monkeypatch.setattr(svc, "_gpu_present", lambda: False)
    monkeypatch.setattr(svc, "discover_ggufs", lambda home=None: [])
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "is_loading", lambda *a, **k: False)
    monkeypatch.setattr(svc, "is_starting", lambda: False)
    monkeypatch.setattr(svc, "_find_llama_binary", lambda state, home: None)
    monkeypatch.setattr(svc, "_resolve_model_path", lambda *a, **k: None)


def test_status_never_wakes_the_host_when_auto_start_is_off(
    tmp_path: Path, offline_status: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, enabled=True, auto_start=False)
    monkeypatch.setattr(
        svc, "wake_rmb_async", lambda home=None: pytest.fail("auto_start is off")
    )
    monkeypatch.setattr(
        svc, "adopt_existing_host", lambda home=None: pytest.fail("auto_start is off")
    )
    status = svc.get_rmb_status({"home_dir": str(tmp_path)})
    assert status["running"] is False
    assert status["ready"] is False
    assert status["auto_start"] is False


def test_status_tells_the_user_which_piece_is_missing(
    tmp_path: Path, offline_status: None
) -> None:
    _seed(tmp_path, enabled=True, auto_start=False)
    status = svc.get_rmb_status({"home_dir": str(tmp_path)})
    assert status["installed"] is False
    assert status["model_present"] is False
    assert status["runtime_present"] is False
    assert "GGUF" in status["not_ready_hint"]


def test_status_clears_a_stuck_vision_suspend_when_nothing_is_running(
    tmp_path: Path, offline_status: None
) -> None:
    """A failed start used to leave SmolVLM disabled for ever."""
    _seed(tmp_path, enabled=True, auto_start=False, vision_suspended=True)
    status = svc.get_rmb_status({"home_dir": str(tmp_path)})
    assert status["vision_suspended"] is False
    assert load_rmb_json(str(tmp_path))["vision_suspended"] is False


def test_status_reports_the_live_gguf_stem_as_the_chat_model(
    tmp_path: Path, offline_status: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status bar must show the file that is loaded, not the catalog id."""
    model = tmp_path / "Qwopus3.5-9B-Coder-MTP-Q4_K_M.gguf"
    model.write_bytes(b"gguf")
    _seed(tmp_path, enabled=True, auto_start=False, model_id="qwen25-coder-7b")
    monkeypatch.setattr(svc, "_resolve_model_path", lambda *a, **k: model)
    status = svc.get_rmb_status({"home_dir": str(tmp_path)})
    assert status["chat_model"] == model.stem
    assert status["model_id"] == model.stem
    assert status["model"]["filename"] == model.name


def test_status_does_not_report_a_pid_it_could_not_find(
    tmp_path: Path, offline_status: None
) -> None:
    _seed(tmp_path, enabled=True, auto_start=False, pid="junk")
    assert svc.get_rmb_status({"home_dir": str(tmp_path)})["pid"] is None


def test_autofit_card_stays_quiet_until_a_fit_has_actually_been_measured(
    tmp_path: Path,
) -> None:
    state = merge_state({"profile": "autofit", "last_autofit": None})
    card = svc._status_autofit(state, None, running=False)
    assert card["enabled"] is True
    assert card["last"] is None
    assert "summary" not in card


def test_the_host_auto_card_prefers_what_was_recorded_at_start(tmp_path: Path) -> None:
    recorded = {"summary": "MTP armed", "mtp": True}
    card = svc._status_host_auto({"host_auto": recorded}, None)
    assert card.get("mtp") is True
    assert "MTP" in str(card.get("summary") or "")
    assert card.get("thinking_mode") == "on"
    # No record yet → a filename-only guess, never a GGUF walk on the 8s poll.
    fresh = svc._status_host_auto({}, None)
    assert isinstance(fresh, dict)
    assert fresh.get("mtp") is False
    assert fresh.get("thinking_mode") == "on"


def test_the_default_port_is_the_rmb_port_not_the_vision_port(tmp_path: Path) -> None:
    """Chat and vision must never share a host — 8740 is SmolVLM's."""
    assert DEFAULT_CHAT_PORT == 8787
    assert merge_state({})["base_url"].endswith(":8787/v1")
