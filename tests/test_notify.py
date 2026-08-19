"""Reach — quiet-hours policy, durable outbox, and the delivery pass."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from remedy.core import notify as N
from remedy.core import reminders as R


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "rhome"


def _at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 8, 17, hour, minute).timestamp()


# --- quiet hours ------------------------------------------------------------


def test_quiet_hours_overnight_wrap() -> None:
    p = N.NotifyPolicy(quiet_start_hour=22, quiet_end_hour=7)
    assert N.in_quiet_hours(p, _at(23)) is True
    assert N.in_quiet_hours(p, _at(3)) is True
    assert N.in_quiet_hours(p, _at(6, 59)) is True
    assert N.in_quiet_hours(p, _at(7)) is False
    assert N.in_quiet_hours(p, _at(14)) is False


def test_quiet_hours_same_day_window() -> None:
    p = N.NotifyPolicy(quiet_start_hour=1, quiet_end_hour=7)
    assert N.in_quiet_hours(p, _at(3)) is True
    assert N.in_quiet_hours(p, _at(23)) is False


def test_quiet_disabled_never_quiet() -> None:
    p = N.NotifyPolicy(quiet_enabled=False)
    assert N.in_quiet_hours(p, _at(3)) is False


def test_quiet_end_is_next_morning() -> None:
    p = N.NotifyPolicy(quiet_start_hour=22, quiet_end_hour=7)
    end = datetime.fromtimestamp(N.quiet_hours_end(p, _at(23)))
    assert (end.day, end.hour) == (18, 7)  # next morning
    end2 = datetime.fromtimestamp(N.quiet_hours_end(p, _at(3)))
    assert (end2.day, end2.hour) == (17, 7)  # later the same morning


# --- decide -----------------------------------------------------------------


def test_decide_delivers_outside_quiet() -> None:
    p = N.NotifyPolicy()
    assert N.decide("low", p, now=_at(14)) == "deliver"
    assert N.decide("high", p, now=_at(14)) == "deliver"


def test_decide_defers_low_importance_in_quiet() -> None:
    p = N.NotifyPolicy(quiet_min_importance="high")
    assert N.decide("normal", p, now=_at(3)) == "defer"
    assert N.decide("low", p, now=_at(3)) == "defer"
    # urgent still gets through
    assert N.decide("high", p, now=_at(3)) == "deliver"


def test_decide_never_drops() -> None:
    p = N.NotifyPolicy()
    assert N.decide("low", p, now=_at(3)) in ("deliver", "defer")


# --- outbox -----------------------------------------------------------------


def test_push_and_list(home) -> None:
    n = N.push_notification("Rent due", home=home)
    assert n is not None and n.read is False
    items = N.list_notifications(home=home)
    assert [i.text for i in items] == ["Rent due"]
    assert N.unread_count(home) == 1


def test_push_dedupes_inside_window(home) -> None:
    t = time.time()
    assert N.push_notification("same", home=home, now=t) is not None
    assert N.push_notification("same", home=home, now=t + 10) is None
    # outside the window it is a genuine new ping
    assert N.push_notification("same", home=home, now=t + 400) is not None
    assert len(N.list_notifications(home=home)) == 2


def test_push_rejects_empty(home) -> None:
    assert N.push_notification("   ", home=home) is None


def test_mark_read(home) -> None:
    a = N.push_notification("one", home=home, now=time.time())
    N.push_notification("two", home=home, now=time.time() + 400)
    assert N.mark_read([a.id], home=home) == 1
    assert N.unread_count(home) == 1
    assert N.mark_read(all_=True, home=home) == 1
    assert N.unread_count(home) == 0


def test_unread_only_filter(home) -> None:
    a = N.push_notification("x", home=home, now=time.time())
    N.push_notification("y", home=home, now=time.time() + 400)
    N.mark_read([a.id], home=home)
    assert [n.text for n in N.list_notifications(unread_only=True, home=home)] == ["y"]


# --- delivery pass ----------------------------------------------------------


def test_deliver_due_sends_and_records(home) -> None:
    R.add_reminder("take meds", time.time() - 5, home=home)
    sent: list[str] = []
    out = N.deliver_due(
        home=home,
        policy=N.NotifyPolicy(quiet_enabled=False),
        messenger_send=sent.append,
    )
    assert out["count"] == 1
    assert sent and "take meds" in sent[0]
    assert N.unread_count(home) == 1
    # claimed — a second pass delivers nothing
    assert N.deliver_due(home=home, policy=N.NotifyPolicy(quiet_enabled=False))["count"] == 0


def test_deliver_defers_in_quiet_hours_without_losing_it(home) -> None:
    R.add_reminder("normal thing", _at(3) - 5, importance="normal", home=home)
    pol = N.NotifyPolicy(quiet_start_hour=22, quiet_end_hour=7)
    out = N.deliver_due(home=home, policy=pol, now=_at(3))
    assert out["count"] == 0
    assert len(out["deferred"]) == 1
    # still pending, rescheduled to the end of quiet hours — never dropped
    live = R.list_reminders(home=home)
    assert len(live) == 1 and live[0].status == R.STATUS_PENDING
    assert live[0].due_ts >= _at(7) - 120


def test_high_importance_breaks_quiet_hours(home) -> None:
    R.add_reminder("URGENT", _at(3) - 5, importance="high", home=home)
    pol = N.NotifyPolicy(quiet_start_hour=22, quiet_end_hour=7)
    out = N.deliver_due(home=home, policy=pol, now=_at(3))
    assert out["count"] == 1


def test_messenger_failure_never_loses_the_outbox(home) -> None:
    R.add_reminder("resilient", time.time() - 5, home=home)

    def boom(_text):
        raise RuntimeError("telegram down")

    out = N.deliver_due(
        home=home, policy=N.NotifyPolicy(quiet_enabled=False), messenger_send=boom
    )
    assert out["count"] == 1
    assert N.unread_count(home) == 1  # durable record survived the failure


def test_messengers_can_be_disabled(home) -> None:
    R.add_reminder("desk only", time.time() - 5, home=home)
    sent: list[str] = []
    N.deliver_due(
        home=home,
        policy=N.NotifyPolicy(quiet_enabled=False, messengers_enabled=False),
        messenger_send=sent.append,
    )
    assert sent == []
    assert N.unread_count(home) == 1


def test_format_reminder_voice() -> None:
    assert N.format_reminder("rent", importance="normal").startswith("Reminder:")
    assert N.format_reminder("fire", importance="high").startswith("Heads up:")


# --- API surface ------------------------------------------------------------


def test_notifications_endpoints(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app

    hm = tmp_path / "apihome"
    monkeypatch.setenv("REMEDY_HOME", str(hm))
    monkeypatch.setattr(
        "remedy.interfaces.routes.status.load_config",
        lambda: {"home_dir": str(hm)},
    )
    N.push_notification("Rent due tomorrow", home=hm, now=time.time())
    N.push_notification("Bins tonight", home=hm, now=time.time() + 400)

    client = TestClient(create_app())
    r = client.get("/api/notifications")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["unread"] == 2
    texts = {n["text"] for n in body["notifications"]}
    assert "Rent due tomorrow" in texts

    nid = body["notifications"][0]["id"]
    r2 = client.post("/api/notifications/read", json={"ids": [nid]})
    assert r2.status_code == 200 and r2.json()["marked"] == 1
    assert client.get("/api/notifications").json()["unread"] == 1

    r3 = client.post("/api/notifications/read", json={"all": True})
    assert r3.json()["unread"] == 0
    assert client.get("/api/notifications", params={"unread_only": True}).json()["count"] == 0


def test_delivery_thread_lifecycle(home) -> None:
    import threading

    ev = threading.Event()
    t = N.start_delivery_thread(home, interval_s=5, stop_event=ev)
    assert t.is_alive()
    ev.set()
    t.join(timeout=6)


def test_the_messenger_bridge_in_api_can_actually_be_called():
    """``api.py`` imports ``suppress``, not ``contextlib``. The reminder bridge
    used the bare module name, so every push raised NameError — and
    ``deliver_due`` wraps the call in its own ``suppress``, so each reminder was
    recorded as delivered while no messenger ever heard about it.

    Compiling the source is the honest check: the bug was a name that only
    existed at call time, which no import-level test would have caught.
    """
    import ast
    import inspect
    from pathlib import Path

    from remedy.interfaces import api

    src = Path(inspect.getsourcefile(api)).read_text(encoding="utf-8")
    tree = ast.parse(src)

    bound = {"contextlib"} & {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    used = {
        node.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "contextlib"
    }
    assert not (used - bound), (
        "api.py uses contextlib.* without importing contextlib — "
        "a NameError that only fires when the reminder bridge runs"
    )


def test_a_failing_messenger_never_stops_the_durable_outbox(tmp_path):
    """The suppress around the push is right — the outbox is what must survive.
    It just must not be the thing that hides a broken bridge."""
    from remedy.core import notify

    boom = []

    def _explode(text: str) -> None:
        boom.append(text)
        raise RuntimeError("messenger down")

    out = notify.deliver_due(home=tmp_path, messenger_send=_explode)
    assert isinstance(out["delivered"], list)
