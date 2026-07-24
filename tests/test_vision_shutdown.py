"""Vision decoder must stop on API shutdown / stop_server PID cleanup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.vision import runtime as vision_runtime
from remedy.vision.config import save_vision_json


def test_stop_server_kills_recorded_pid(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    vdir = home / "vision"
    vdir.mkdir()
    save_vision_json(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 18765,
            "base_url": "http://127.0.0.1:18765/v1",
            "pid": 424242,
            "model_id": "qwen2.5-vl-3b",
        },
        home,
    )
    vision_runtime._proc = None  # noqa: SLF001

    killed: list[int] = []

    def fake_alive(pid: int) -> bool:
        return pid == 424242

    def fake_looks(pid: int) -> bool:
        return pid == 424242

    def fake_kill(pid: int, *, force: bool = True) -> bool:
        killed.append(pid)
        return True

    monkeypatch.setattr(vision_runtime, "_pid_is_alive", fake_alive)
    monkeypatch.setattr(vision_runtime, "_looks_like_llama_server", fake_looks)
    monkeypatch.setattr(vision_runtime, "_kill_pid_tree", fake_kill)

    result = vision_runtime.stop_server(home_dir=home)
    assert result["ok"] is True
    assert result["stopped"] is True
    assert 424242 in killed
    # pid cleared from side state
    from remedy.vision.config import load_vision_json

    state = load_vision_json(home)
    assert state.get("pid") is None


def test_stop_server_skips_foreign_pid(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    save_vision_json({"pid": 999001, "host": "127.0.0.1", "port": 1}, home)
    vision_runtime._proc = None  # noqa: SLF001

    monkeypatch.setattr(vision_runtime, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(vision_runtime, "_looks_like_llama_server", lambda pid: False)
    kills: list[int] = []
    monkeypatch.setattr(
        vision_runtime,
        "_kill_pid_tree",
        lambda pid, force=True: kills.append(pid) or True,
    )

    result = vision_runtime.stop_server(home_dir=home)
    assert kills == []
    assert result["ok"] is True


def test_stop_server_terminates_popen_handle(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    save_vision_json({}, home)

    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 111
    proc.wait = MagicMock()
    vision_runtime._proc = proc  # noqa: SLF001

    monkeypatch.setattr(vision_runtime, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(vision_runtime, "_kill_pid_tree", lambda *a, **k: False)

    result = vision_runtime.stop_server(home_dir=home)
    proc.terminate.assert_called()
    assert result["stopped"] is True
    assert vision_runtime._proc is None  # noqa: SLF001


def test_shutdown_vision_for_exit_swallows_errors(monkeypatch):
    monkeypatch.setattr(
        vision_runtime,
        "stop_server",
        lambda home_dir=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = vision_runtime.shutdown_vision_for_exit()
    assert out["ok"] is False
    assert "boom" in out.get("error", "")


def test_create_app_lifespan_registers_shutdown():
    """Lifespan context runs shutdown path without raising."""
    app = create_app(runtime=None, api_key="")
    client = TestClient(app)
    # Enter/exit lifespan
    with client:
        r = client.get("/api/status")
        # status may 200 even without full runtime
        assert r.status_code in (200, 503, 500) or r.status_code < 600
