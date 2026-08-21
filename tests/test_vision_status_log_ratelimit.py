"""vision get_status slow-log: WARN only when running, >= 1 s, once per 5 min."""

from __future__ import annotations

import logging
import os
import tempfile

os.environ.setdefault("REMEDY_HOME", tempfile.mkdtemp(prefix="remedy-vision-log-"))

import pytest  # noqa: E402

from remedy.vision import service  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    service._status_slow_last_warn.clear()
    yield
    service._status_slow_last_warn.clear()


def _warns(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_not_running_never_warns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="remedy.vision.service")
    for i in range(50):
        service._log_status_timing(160.0, light=True, installed=True, running=False, now=float(i))
        service._log_status_timing(5_000.0, light=True, installed=True, running=False, now=float(i))
    assert _warns(caplog) == []
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_below_threshold_is_debug_even_when_running(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="remedy.vision.service")
    service._log_status_timing(999.0, light=True, installed=True, running=True, now=0.0)
    assert _warns(caplog) == []


def test_running_slow_warns_once_per_five_minutes(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="remedy.vision.service")
    t = 1000.0
    for i in range(120):  # 2.5 s cadence for 5 minutes
        service._log_status_timing(1_500.0, light=True, installed=True, running=True, now=t + i * 2.5)
    assert len(_warns(caplog)) == 1
    service._log_status_timing(1_500.0, light=True, installed=True, running=True, now=t + 300.0)
    assert len(_warns(caplog)) == 2
    # Different state key gets its own slot.
    service._log_status_timing(1_500.0, light=False, installed=True, running=True, now=t + 301.0)
    assert len(_warns(caplog)) == 3
    assert all("slow" in m for m in _warns(caplog))
