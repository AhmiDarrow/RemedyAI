"""The in-process computer host that stands in for Desktop during CLI chat.

This is the component that takes jobs off the filesystem queue and *acts on the
machine* — opens URLs, drives windows. Two things matter most: it must not act
on anything it was not handed, and a job that fails must be completed as failed
rather than left pending, because a job nobody completes makes every waiting
tool sit out its full timeout.

No test here performs a real OS action; every action path is intercepted.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from remedy.core.computer import cli_host as CH


class FakeJob:
    def __init__(self, action: str, payload: dict | None = None, jid: str = "j1") -> None:
        self.action = action
        self.payload = payload or {}
        self.id = jid


class FakeBridge:
    def __init__(self) -> None:
        self.root = Path(".")
        self.completed: list[dict] = []
        self.alive_calls = 0
        self._connected = False

    def mark_host_alive(self, *, poller: bool = False) -> None:
        self.alive_calls += 1
        self._connected = True

    def host_connected(self, **_kw) -> bool:
        return self._connected

    def pending_count(self) -> int:
        return 0

    def take_ui_command(self):
        return None

    def claim_next(self):
        return None

    def complete(self, job_id, *, ok=True, result=None, error=None):
        self.completed.append({"id": job_id, "ok": ok, "result": result, "error": error})


@pytest.fixture()
def bridge(monkeypatch):
    b = FakeBridge()
    monkeypatch.setattr(
        "remedy.core.computer.host_bridge.get_host_bridge", lambda *a, **kw: b
    )
    return b


@pytest.fixture()
def host(tmp_path):
    h = CH.LocalComputerHost(home_dir=tmp_path)
    yield h
    h.stop(force=True)


# --- lifecycle --------------------------------------------------------------


def test_a_fresh_host_is_not_running(host):
    assert host.running is False


def test_stopping_a_host_that_never_started_succeeds(host):
    assert host.stop() is True


def test_start_then_stop_leaves_no_worker(tmp_path, bridge):
    h = CH.LocalComputerHost(home_dir=tmp_path)
    assert h.start() is True
    assert h.running is True
    assert h.stop(timeout=3.0) is True
    assert h.running is False


def test_starting_twice_does_not_put_two_workers_on_one_queue(tmp_path, bridge):
    """Two pollers claiming the same jobs would double-execute them."""
    h = CH.LocalComputerHost(home_dir=tmp_path)
    h.start()
    before = threading.active_count()
    assert h.start() is True
    assert threading.active_count() == before
    h.stop(timeout=3.0)


def test_a_wedged_worker_is_reported_rather_than_abandoned(host):
    """The running flag must not lie: a thread we could not join is still out there."""

    class Stuck:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    host._thread = Stuck()
    assert host.stop(timeout=0.01) is False
    assert host._thread is not None


def test_force_abandons_a_wedged_worker(host):
    class Stuck:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    host._thread = Stuck()
    assert host.stop(timeout=0.01, force=True) is True
    assert host._thread is None


def test_status_answers_before_the_host_ever_runs(host, bridge):
    st = host.status()
    assert st["mode"] == "cli"
    assert st["running"] is False
    assert st["jobs_completed"] == 0
    assert st["home"]


# --- job handling -----------------------------------------------------------


def test_a_successful_job_is_completed_ok(host, monkeypatch):
    b = FakeBridge()
    monkeypatch.setattr(host, "_run_action", lambda act, payload, br: {"ok": True})
    host._handle_job(b, FakeJob("page_text"))
    assert b.completed[0]["ok"] is True
    assert host.jobs_completed == 1
    assert host.last_error == ""


def test_a_refused_job_is_completed_as_failed_not_left_pending(host, monkeypatch):
    """A job nobody completes makes every waiting tool burn its whole timeout."""
    b = FakeBridge()
    monkeypatch.setattr(
        host, "_run_action", lambda act, payload, br: {"ok": False, "message": "no browser"}
    )
    host._handle_job(b, FakeJob("navigate"))
    assert b.completed[0]["ok"] is False
    assert b.completed[0]["error"] == "no browser"
    assert host.jobs_completed == 0
    assert host.last_error == "no browser"


def test_a_crashing_action_still_completes_the_job(host, monkeypatch):
    b = FakeBridge()

    def boom(*a, **kw):
        raise RuntimeError("UIA is not available")

    monkeypatch.setattr(host, "_run_action", boom)
    host._handle_job(b, FakeJob("snapshot"))
    assert b.completed[0]["ok"] is False
    assert "UIA is not available" in b.completed[0]["error"]


def test_a_result_without_an_ok_flag_counts_as_success(host, monkeypatch):
    b = FakeBridge()
    monkeypatch.setattr(host, "_run_action", lambda *a: {"text": "x"})
    host._handle_job(b, FakeJob("page_text"))
    assert b.completed[0]["ok"] is True


def test_the_last_action_is_recorded_for_status(host, monkeypatch):
    b = FakeBridge()
    monkeypatch.setattr(host, "_run_action", lambda *a: {"ok": True})
    host._handle_job(b, FakeJob("screenshot"))
    assert host.last_action == "screenshot"


# --- UI commands: the path that opens a real browser ------------------------


def _patch_native_open_url(monkeypatch, fn):
    """cli_host uses desktop_os.native() — patch whichever OS module is live."""
    from remedy.core.computer.desktop_os import native

    monkeypatch.setattr(native(), "open_url", fn)


def test_a_ui_open_command_opens_the_url(host, monkeypatch):
    opened: list[str] = []
    _patch_native_open_url(monkeypatch, lambda u: opened.append(u))
    host._handle_ui(FakeBridge(), {"action": "open_browser", "url": "https://example.com"})
    assert len(opened) == 1
    assert opened[0].startswith("https://example.com")


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/System32/config",
        "javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "not a url at all",
    ],
)
def test_a_dangerous_or_malformed_url_is_refused(host, monkeypatch, url):
    """The queue is a file on disk; anything that can write it can ask for a URL."""
    _patch_native_open_url(monkeypatch, lambda u: pytest.fail(f"opened {u!r}"))
    host._handle_ui(FakeBridge(), {"action": "open_browser", "url": url})
    assert host.last_error


def test_a_ui_command_with_no_url_does_nothing(host, monkeypatch):
    _patch_native_open_url(monkeypatch, lambda u: pytest.fail("opened nothing"))
    host._handle_ui(FakeBridge(), {"action": "open_browser", "url": ""})


def test_an_unrelated_ui_command_is_ignored(host, monkeypatch):
    _patch_native_open_url(monkeypatch, lambda u: pytest.fail("should not open"))
    host._handle_ui(FakeBridge(), {"action": "focus_window", "url": "https://x.example"})


def test_a_failure_to_open_is_recorded_not_raised(host, monkeypatch):
    def boom(_u):
        raise OSError("no browser registered")

    _patch_native_open_url(monkeypatch, boom)
    host._handle_ui(FakeBridge(), {"action": "navigate", "url": "https://example.com"})
    assert "no browser registered" in host.last_error


# --- the shared singleton ---------------------------------------------------


def test_the_shared_host_is_one_object(tmp_path):
    assert CH.get_local_computer_host(tmp_path) is CH.get_local_computer_host(tmp_path)


def test_a_later_home_fills_in_an_unset_one(tmp_path, monkeypatch):
    monkeypatch.setattr(CH, "_local_host", None)
    first = CH.get_local_computer_host(None)
    assert first.home_dir is None
    assert CH.get_local_computer_host(tmp_path).home_dir == tmp_path


def test_a_later_home_does_not_move_an_existing_host(tmp_path, monkeypatch):
    """Repointing a live poller at another queue would strand the first one."""
    monkeypatch.setattr(CH, "_local_host", None)
    first = CH.get_local_computer_host(tmp_path)
    assert CH.get_local_computer_host(tmp_path / "elsewhere").home_dir == tmp_path
    assert first.home_dir == tmp_path


def test_stopping_a_host_that_was_never_created_is_fine(monkeypatch):
    monkeypatch.setattr(CH, "_local_host", None)
    assert CH.stop_cli_computer_host() is True


def test_the_singleton_survives_a_stop_so_status_still_answers(tmp_path, monkeypatch, bridge):
    monkeypatch.setattr(CH, "_local_host", None)
    host = CH.start_cli_computer_host(tmp_path)
    host.jobs_completed = 4
    assert CH.stop_cli_computer_host(timeout=3.0) is True
    assert CH._local_host is host
    assert host.status()["jobs_completed"] == 4
