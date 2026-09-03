"""Implicit RMB starts must not spawn a host that was never set up.

On 2026-09-03 an xAI 503 ("Service temporarily unavailable") was read by the
ReAct loop as the local host loading weights. ``wait_rmb_ready`` then forced
the watchdog on, unloaded the SmolVLM vision host and started a 7B GGUF — on a
turn bound to grok. Two guards stop that class of failure:

* ``rmb_is_set_up`` — the owner has an enabled rmb.json pointing at a GGUF
  that exists. Without it, ``wait_rmb_ready`` and ``wake_rmb_async`` return a
  refusal before touching the watchdog or spawning anything.
* ``is_local_host_loading_error`` — the 503 "loading" branch of the HTTP loop
  only fires when the *binding* is local, whatever the body says.

Nothing here spawns a process; the autouse fixture fails the test if it tries.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from remedy.core.react_loop.errors import is_local_host_loading_error
from remedy.runtime.rmb import service as svc
from remedy.runtime.rmb.config import merge_state, save_rmb_json

_XAI_503 = (
    '{"code":"unavailable","error":"Service temporarily unavailable. '
    'The model did not respond to this request."}'
)


class _Recorder:
    def __init__(self) -> None:
        self.watchdog_calls: list[dict[str, Any]] = []
        self.start_calls: list[dict[str, Any]] = []
        self.stop_calls: int = 0

    def watchdog(self, *a: Any, **k: Any) -> None:
        self.watchdog_calls.append(dict(k))

    def start(self, *a: Any, **k: Any) -> dict[str, Any]:
        self.start_calls.append(dict(k))
        return {"ok": False, "error": "recorded"}

    def stop(self, *a: Any, **k: Any) -> dict[str, Any]:
        self.stop_calls += 1
        return {"ok": True}


@pytest.fixture(autouse=True)
def cold_host(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """A machine where nothing RMB-shaped is running, with every spawn recorded."""
    rec = _Recorder()

    def _no_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"test tried to spawn a real process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    monkeypatch.setattr(svc, "ensure_rmb_watchdog", rec.watchdog)
    monkeypatch.setattr(svc, "start_rmb_server", rec.start)
    monkeypatch.setattr(svc, "stop_rmb_server", rec.stop)
    monkeypatch.setattr(svc, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(svc, "is_starting", lambda *a, **k: False)
    monkeypatch.setattr(svc, "is_loading", lambda *a, **k: False)
    monkeypatch.setattr(svc, "loading_stalled", lambda *a, **k: False)
    monkeypatch.setattr(svc, "managed_process_alive", lambda *a, **k: False)
    monkeypatch.setattr(svc, "adopt_existing_host", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(svc, "_refresh_user_stopped", lambda *a, **k: False)
    # The resolver also scans ~/Downloads and the sibling Muscle Bridge folder;
    # on the owner's box those hold real GGUFs. Only the test home counts here.
    monkeypatch.setattr(svc, "_model_search_roots", lambda home: [Path(str(home)) / "models"])
    svc._proc = None
    svc._user_stopped = False
    yield rec
    svc._proc = None
    svc._user_stopped = False


def _set_up(home: Path, *, enabled: bool = True, gguf_exists: bool = True) -> Path:
    gguf = home / "models" / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    if gguf_exists:
        gguf.parent.mkdir(parents=True, exist_ok=True)
        gguf.write_bytes(b"GGUF")
    state = merge_state(
        {
            "enabled": enabled,
            "model_id": "Qwen2.5-Coder-7B-Instruct-Q4_K_M",
            "model_path": str(gguf),
        }
    )
    save_rmb_json(state, str(home))
    return gguf


# --- rmb_is_set_up -----------------------------------------------------------


def test_fresh_home_is_not_set_up(tmp_path: Path) -> None:
    assert svc.rmb_is_set_up(tmp_path) is False


def test_disabled_state_is_not_set_up(tmp_path: Path) -> None:
    _set_up(tmp_path, enabled=False)
    assert svc.rmb_is_set_up(tmp_path) is False


def test_enabled_without_gguf_is_not_set_up(tmp_path: Path) -> None:
    _set_up(tmp_path, gguf_exists=False)
    assert svc.rmb_is_set_up(tmp_path) is False


def test_enabled_with_gguf_is_set_up(tmp_path: Path) -> None:
    _set_up(tmp_path)
    assert svc.rmb_is_set_up(tmp_path) is True


# --- wait_rmb_ready / wake_rmb_async ------------------------------------------


def test_wait_rmb_ready_refuses_on_fresh_home(tmp_path: Path, cold_host: _Recorder) -> None:
    r = svc.wait_rmb_ready(tmp_path, timeout_s=1.0)
    assert r["ok"] is False
    assert r.get("not_set_up") is True
    assert "not set up" in r["error"]
    assert cold_host.watchdog_calls == []
    assert cold_host.start_calls == []
    assert cold_host.stop_calls == 0


def test_wake_rmb_async_refuses_on_fresh_home(tmp_path: Path, cold_host: _Recorder) -> None:
    r = svc.wake_rmb_async(tmp_path)
    assert r["ok"] is False
    assert r.get("not_set_up") is True
    assert cold_host.watchdog_calls == []
    assert cold_host.start_calls == []


def test_wait_rmb_ready_refuses_when_gguf_missing(tmp_path: Path, cold_host: _Recorder) -> None:
    _set_up(tmp_path, gguf_exists=False)
    r = svc.wait_rmb_ready(tmp_path, timeout_s=1.0)
    assert r["ok"] is False
    assert r.get("not_set_up") is True
    assert cold_host.start_calls == []


def test_wait_rmb_ready_starts_when_set_up(tmp_path: Path, cold_host: _Recorder) -> None:
    _set_up(tmp_path)
    r = svc.wait_rmb_ready(tmp_path, timeout_s=12.0, poll_s=0.05)
    # The recorded start "fails", so the wait times out — but it *tried*
    # (sync start first, then the async kick; both go through start_rmb_server).
    assert r["ok"] is False
    assert r.get("not_set_up") is None
    assert len(cold_host.start_calls) >= 1
    assert cold_host.watchdog_calls and cold_host.watchdog_calls[0].get("force") is True


def test_wake_rmb_async_starts_when_set_up(tmp_path: Path, cold_host: _Recorder) -> None:
    _set_up(tmp_path)
    r = svc.wake_rmb_async(tmp_path)
    assert r["ok"] is True and r.get("starting") is True
    # Background thread — give it a moment to hit the recorder.
    import time

    for _ in range(50):
        if cold_host.start_calls:
            break
        time.sleep(0.02)
    assert len(cold_host.start_calls) == 1


def test_wait_rmb_ready_still_waits_on_host_already_loading(
    tmp_path: Path, cold_host: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host the owner started (loading now) may be waited on even without rmb.json."""
    monkeypatch.setattr(svc, "is_loading", lambda *a, **k: True)
    r = svc.wait_rmb_ready(tmp_path, timeout_s=1.0, poll_s=0.05)
    assert r.get("not_set_up") is None
    assert cold_host.watchdog_calls  # the normal path ran
    assert cold_host.start_calls == []  # and did not spawn a second host


# --- is_local_host_loading_error ---------------------------------------------


def test_cloud_503_unavailable_is_not_local_loading() -> None:
    assert (
        is_local_host_loading_error(
            503, _XAI_503, provider="xai", model="grok-4.5", base_url="https://api.x.ai/v1"
        )
        is False
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "deepseek", "openrouter"])
def test_other_cloud_5xx_with_loading_words_is_not_local(provider: str) -> None:
    assert is_local_host_loading_error(502, "model not ready", provider=provider) is False
    assert is_local_host_loading_error(503, "Loading model", provider=provider) is False


def test_rmb_503_loading_model_is_local_loading() -> None:
    assert (
        is_local_host_loading_error(
            503,
            '{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}',
            provider="rmb",
            model="Qwen2.5-Coder-7B-Instruct-Q4_K_M",
            base_url="http://127.0.0.1:8787/v1",
        )
        is True
    )


def test_loopback_custom_host_503_is_local_loading() -> None:
    assert (
        is_local_host_loading_error(
            503, "Loading model", provider="custom", base_url="http://127.0.0.1:8080/v1"
        )
        is True
    )


def test_local_503_without_loading_words_is_not_loading() -> None:
    assert is_local_host_loading_error(503, "out of memory", provider="rmb") is False


def test_non_5xx_is_never_loading() -> None:
    assert is_local_host_loading_error(429, "unavailable", provider="rmb") is False
    assert is_local_host_loading_error(500, "unavailable", provider="rmb") is False
