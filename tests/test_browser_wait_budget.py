"""How long a browser tool waits, and why it depends on the host.

A job in the queue carries an outstanding `ui_command`. `wait()` reads that as
"the host is about to run this" and declines to fail fast — which is right when
a host exists and exactly wrong when none does, because with Desktop closed
nobody ever takes the command and the stale file looks like progress forever.
So the tool sat out its whole budget: 22s for a navigate, 30s for page_text.

The fix keys off `host_connected()`, which makes the risk the opposite one —
shortening the budget for a host that *is* there and merely slow. Both
directions are pinned here.
"""

from __future__ import annotations

import pytest

from remedy.core.computer.executor import ComputerExecutor


class RecordingBridge:
    """Captures the wait() budget without a queue or a host."""

    def __init__(self, *, connected: bool) -> None:
        self._connected = connected
        self.waits: list[dict] = []
        self.root = "."

    def host_connected(self, **_kw) -> bool:
        return self._connected

    def settle_after_navigate(self, **_kw):
        return 0.0

    def wait(self, job_id, **kw):
        self.waits.append(kw)
        return type(
            "Job", (), {"status": "error", "result": None, "error": "no host", "id": job_id}
        )()

    def __getattr__(self, _name):
        return lambda *a, **kw: None


@pytest.fixture()
def executor(tmp_path, monkeypatch):
    def make(*, connected: bool):
        ex = ComputerExecutor(home_dir=tmp_path)
        bridge = RecordingBridge(connected=connected)
        ex.bridge = bridge
        monkeypatch.setattr(ex, "_enqueue", lambda action, payload: type("J", (), {"id": "j1"})())
        monkeypatch.setattr(ex, "_abort_check", lambda: False)
        return ex, bridge

    return make


def _budgets(bridge):
    return [(w.get("timeout_s"), w.get("unclaimed_timeout_s")) for w in bridge.waits]


# --- page_text ---------------------------------------------------------------


def test_a_live_host_keeps_the_full_page_text_budget(executor):
    """The host runs up to two 9s rail evals; cutting that loses real answers."""
    ex, bridge = executor(connected=True)
    ex._browser_page_text()
    assert _budgets(bridge)[0] == (14.0, 5.0)


def test_no_host_does_not_sit_out_the_full_budget(executor):
    ex, bridge = executor(connected=False)
    ex._browser_page_text()
    timeout, unclaimed = _budgets(bridge)[0]
    assert timeout < 14.0
    assert unclaimed < 5.0


def test_both_attempts_still_run_with_no_host(executor):
    """The retry is preserved — it is the waiting that was cut, not the try."""
    ex, bridge = executor(connected=False)
    ex._browser_page_text()
    assert len(bridge.waits) == 2


def test_both_attempts_still_run_with_a_live_host(executor):
    ex, bridge = executor(connected=True)
    ex._browser_page_text()
    assert len(bridge.waits) == 2


def test_the_whole_no_host_page_text_path_is_bounded_well_under_the_old_cost(executor):
    """It used to be 2 x 14s plus settle. The owner watched 30 seconds pass."""
    ex, bridge = executor(connected=False)
    ex._browser_page_text()
    assert sum(t for t, _ in _budgets(bridge)) <= 10.0


def test_a_failed_page_text_still_says_what_to_do(executor):
    ex, _ = executor(connected=False)
    out = ex._browser_page_text()
    assert out.get("ok") is False
    assert out.get("message")
