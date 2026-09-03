"""Every way out of ``remedy serve`` leaves a breadcrumb in the logs."""

from __future__ import annotations

import faulthandler
import logging
import signal
import threading

import pytest

from remedy.interfaces import serve_forensics


def test_crash_forensics_enables_faulthandler_into_crash_log(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_forensics, "_crash_file", None)
    prior = threading.excepthook
    try:
        path = serve_forensics.enable_crash_forensics(tmp_path)
        assert path == tmp_path / "logs" / "crash.log"
        assert path is not None and path.exists()
        assert faulthandler.is_enabled()
        assert threading.excepthook is not prior
    finally:
        faulthandler.disable()
        threading.excepthook = prior


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_uncaught_thread_exception_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(serve_forensics, "_crash_file", None)
    prior = threading.excepthook
    try:
        serve_forensics.enable_crash_forensics(tmp_path)
        with caplog.at_level(logging.ERROR, logger="remedy.serve"):
            t = threading.Thread(target=lambda: 1 / 0, name="boom-thread")
            t.start()
            t.join(2)
        assert any("boom-thread" in r.getMessage() for r in caplog.records)
    finally:
        faulthandler.disable()
        threading.excepthook = prior


def test_logged_server_records_exit_signal(caplog):
    uvicorn = pytest.importorskip("uvicorn")

    async def app(scope, receive, send):  # pragma: no cover - never served
        pass

    # Build the same subclass run_uvicorn_logged uses, without binding a port.
    config = uvicorn.Config(app, host="127.0.0.1", port=0)
    captured: dict[str, object] = {}

    def _fake_run(self):
        self.started = True
        with caplog.at_level(logging.CRITICAL, logger="remedy.serve"):
            self.handle_exit(signal.SIGTERM, None)
        captured["should_exit"] = self.should_exit

    monkeypatch_target = uvicorn.Server
    original_run = monkeypatch_target.run
    try:
        monkeypatch_target.run = _fake_run
        with caplog.at_level(logging.WARNING, logger="remedy.serve"):
            serve_forensics.run_uvicorn_logged(app, host="127.0.0.1", port=0)
    finally:
        monkeypatch_target.run = original_run
    assert captured["should_exit"] is True
    messages = [r.getMessage() for r in caplog.records]
    assert any("SIGTERM" in m for m in messages)
    assert any("API server loop ended" in m for m in messages)
    _ = config
