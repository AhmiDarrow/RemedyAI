"""Crash forensics helpers (workers only — not a Python :7400 server)."""

from __future__ import annotations

import faulthandler
import logging
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


def test_run_uvicorn_logged_removed() -> None:
    """Regression: no Python uvicorn serve helper after runtime cutover."""
    assert not hasattr(serve_forensics, "run_uvicorn_logged")
