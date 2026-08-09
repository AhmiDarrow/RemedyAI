"""RMB rock-solid reliability: loading stall, crash-loop, port wait, single-flight helpers."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from remedy.runtime.rmb import service as svc


@pytest.fixture(autouse=True)
def _reset_reliability_state():
    """Keep module globals isolated across tests."""
    svc._loading_since = 0.0
    svc._watchdog_restart_times = []
    svc._watchdog_fail_streak = 0
    svc._last_start_error = None
    svc._last_health_detail = ""
    svc._start_flight_active = False
    svc._start_flight_result = None
    svc._start_flight_event.set()
    svc.invalidate_cache()
    yield
    svc._loading_since = 0.0
    svc._watchdog_restart_times = []
    svc._start_flight_active = False


def test_note_loading_state_tracks_duration():
    svc._note_loading_state(True)
    assert svc._loading_since > 0
    t0 = svc._loading_since
    time.sleep(0.05)
    svc._note_loading_state(True)  # sticky start
    assert svc._loading_since == t0
    svc._note_loading_state(False)
    assert svc._loading_since == 0.0


def test_loading_stalled_only_after_deadline():
    svc._loading_since = time.time() - 10
    with patch.object(svc, "is_loading", return_value=True):
        assert svc.loading_for_s() >= 9.0
        assert svc.loading_stalled(max_s=60) is False
    svc._loading_since = time.time() - 200
    with patch.object(svc, "is_loading", return_value=True):
        assert svc.loading_stalled(max_s=180) is True


def test_watchdog_crash_loop_backoff():
    assert svc._watchdog_can_restart() is True
    now = time.time()
    svc._watchdog_restart_times = [now - 10, now - 20, now - 30, now - 40]
    assert svc._watchdog_can_restart() is False
    # Old restarts fall out of window
    svc._watchdog_restart_times = [now - 400, now - 350]
    assert svc._watchdog_can_restart() is True
    svc._watchdog_record_restart()
    assert len(svc._watchdog_restart_times) >= 1


def test_tail_log(tmp_path: Path):
    p = tmp_path / "llama-server.log"
    p.write_text("alpha\n" + ("x" * 100) + "\nTAIL_MARKER\n", encoding="utf-8")
    tail = svc._tail_log(p, max_bytes=80)
    assert "TAIL_MARKER" in tail
    assert svc._tail_log(tmp_path / "missing.log") == ""


def test_resolve_occupied_port_free():
    with patch.object(svc, "_port_open", return_value=False):
        r = svc._resolve_occupied_port("127.0.0.1", 8787, "http://127.0.0.1:8787/v1")
    assert r["ok"] is True
    assert r.get("free") is True


def test_resolve_occupied_port_already_healthy():
    with (
        patch.object(svc, "_port_open", return_value=True),
        patch.object(svc, "_health", return_value=True),
    ):
        r = svc._resolve_occupied_port("127.0.0.1", 8787, "http://127.0.0.1:8787/v1")
    assert r["ok"] is True
    assert r.get("already_healthy") is True


def test_resolve_occupied_port_waits_then_healthy():
    calls = {"n": 0}

    def health(_base, timeout=0.9):  # noqa: ARG001
        calls["n"] += 1
        return calls["n"] >= 3

    with (
        patch.object(svc, "_port_open", return_value=True),
        patch.object(svc, "_health", side_effect=health),
        patch.object(svc, "_find_pid_on_port", return_value=12345),
        patch.object(svc, "_looks_like_llama_server", return_value=True),
        patch.object(svc, "time") as mock_time,
    ):
        # Control sleep so test is instant
        mock_time.time.side_effect = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 1.0]
        mock_time.sleep = lambda _s: None
        # Also patch module-level time used in wait loop — service uses time.time/sleep
        # from the imported module; re-patch at svc.time level is wrong if imported as
        # `import time`. Patch time.sleep / use real short wait instead.

    # Prefer real short wait with mocked health
    with (
        patch.object(svc, "_port_open", return_value=True),
        patch.object(svc, "_health", side_effect=health),
        patch.object(svc, "_find_pid_on_port", return_value=12345),
        patch.object(svc, "_looks_like_llama_server", return_value=True),
        patch("remedy.runtime.rmb.service.time.sleep", return_value=None),
    ):
        r = svc._resolve_occupied_port(
            "127.0.0.1", 8787, "http://127.0.0.1:8787/v1", wait_s=5.0
        )
    assert r["ok"] is True
    assert r.get("became_healthy") is True


def test_resolve_occupied_port_clears_wedged_llama():
    with (
        patch.object(svc, "_port_open", side_effect=[True, True, False]),
        patch.object(svc, "_health", return_value=False),
        patch.object(svc, "_find_pid_on_port", return_value=99),
        patch.object(svc, "_looks_like_llama_server", return_value=True),
        patch.object(svc, "_wait_for_port_healthy", return_value=False),
        patch.object(svc, "_kill_pid", return_value=True) as kill,
        patch.object(svc, "_kill_listeners_on_port", return_value=1),
        patch("remedy.runtime.rmb.service.time.sleep", return_value=None),
    ):
        r = svc._resolve_occupied_port(
            "127.0.0.1", 8787, "http://127.0.0.1:8787/v1", wait_s=2.0
        )
    assert r["ok"] is True
    assert r.get("cleared") is True
    kill.assert_called()


def test_health_treats_503_as_not_ready():
    from urllib.error import HTTPError

    def boom(*_a, **_k):
        raise HTTPError("http://x/health", 503, "Loading", hdrs=None, fp=None)  # type: ignore[arg-type]

    with (
        patch("remedy.core.security.is_loopback_service_url", return_value=True),
        patch("remedy.core.security.urlopen_no_redirect", side_effect=boom),
    ):
        assert svc._health("http://127.0.0.1:8787/v1") is False
    assert svc._last_health_detail == "loading"


def test_dead_child_invalidates_running_cache():
    class Dead:
        def poll(self):
            return 1

    svc._proc = Dead()  # type: ignore[assignment]
    svc._running_cache["ts"] = time.time()
    svc._running_cache["value"] = True
    svc._running_cache["key"] = "127.0.0.1:8787:h"
    with (
        patch.object(svc, "_port_open", return_value=False),
        patch.object(svc, "load_rmb_json", return_value={}),
        patch.object(
            svc,
            "merge_state",
            return_value={"host": "127.0.0.1", "port": 8787, "base_url": "http://127.0.0.1:8787/v1"},
        ),
    ):
        assert svc.is_running(force=False, require_http=True) is False
    svc._proc = None
