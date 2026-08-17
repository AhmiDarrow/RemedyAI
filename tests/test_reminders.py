"""Remedy's clock — parsing, recurrence, firing, and the bill seed."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from remedy.core import reminders as R


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "rhome"


# --- parse_when -------------------------------------------------------------


def test_parse_relative_units() -> None:
    now = datetime(2026, 8, 17, 10, 0, 0).timestamp()
    assert R.parse_when("in 30m", now=now) == pytest.approx(now + 1800, abs=2)
    assert R.parse_when("in 2 hours", now=now) == pytest.approx(now + 7200, abs=2)
    assert R.parse_when("in 3 days", now=now) == pytest.approx(now + 259200, abs=2)
    assert R.parse_when("in 1 week", now=now) == pytest.approx(now + 604800, abs=2)


def test_parse_iso_and_date_only() -> None:
    ts = R.parse_when("2026-09-01T14:30:00")
    assert datetime.fromtimestamp(ts).hour == 14
    # date-only defaults to a sane morning hour, not midnight
    d = datetime.fromtimestamp(R.parse_when("2026-09-01"))
    assert (d.year, d.month, d.day, d.hour) == (2026, 9, 1, 9)


def test_parse_tomorrow_and_today_with_time() -> None:
    now = datetime(2026, 8, 17, 10, 0, 0).timestamp()
    tm = datetime.fromtimestamp(R.parse_when("tomorrow 5pm", now=now))
    assert (tm.day, tm.hour) == (18, 17)
    td = datetime.fromtimestamp(R.parse_when("today 11:30", now=now))
    assert (td.day, td.hour, td.minute) == (17, 11, 30)


def test_parse_weekday_rolls_forward() -> None:
    # 2026-08-17 is a Monday
    now = datetime(2026, 8, 17, 10, 0, 0).timestamp()
    fri = datetime.fromtimestamp(R.parse_when("friday 9am", now=now))
    assert fri.weekday() == 4 and fri.day == 21
    # same weekday means NEXT week, not today
    mon = datetime.fromtimestamp(R.parse_when("monday", now=now))
    assert mon.day == 24


def test_parse_bare_time_rolls_to_tomorrow_when_past() -> None:
    now = datetime(2026, 8, 17, 18, 0, 0).timestamp()
    t = datetime.fromtimestamp(R.parse_when("9am", now=now))
    assert (t.day, t.hour) == (18, 9)


def test_parse_rejects_garbage() -> None:
    assert R.parse_when("") is None
    assert R.parse_when("sometime later maybe") is None


# --- recurrence -------------------------------------------------------------


def test_next_occurrence_units() -> None:
    base = datetime(2026, 8, 17, 9, 0).timestamp()
    assert R.next_occurrence(base, "") is None
    assert R.next_occurrence(base, "daily") == pytest.approx(base + 86400, abs=2)
    assert R.next_occurrence(base, "weekly") == pytest.approx(base + 604800, abs=2)
    m = datetime.fromtimestamp(R.next_occurrence(base, "monthly"))
    assert (m.month, m.day) == (9, 17)
    y = datetime.fromtimestamp(R.next_occurrence(base, "yearly"))
    assert y.year == 2027


def test_monthly_clamps_short_months() -> None:
    jan31 = datetime(2026, 1, 31, 9, 0).timestamp()
    feb = datetime.fromtimestamp(R.next_occurrence(jan31, "monthly"))
    assert (feb.month, feb.day) == (2, 28)


def test_monthly_wraps_year() -> None:
    dec = datetime(2026, 12, 15, 9, 0).timestamp()
    nxt = datetime.fromtimestamp(R.next_occurrence(dec, "monthly"))
    assert (nxt.year, nxt.month) == (2027, 1)


# --- store round-trip -------------------------------------------------------


def test_add_and_list(home) -> None:
    r = R.add_reminder("take the bins out", "in 1 hour", home=home)
    assert r is not None and r.status == R.STATUS_PENDING
    items = R.list_reminders(home=home)
    assert [i.text for i in items] == ["take the bins out"]


def test_add_rejects_empty_or_unparsable(home) -> None:
    assert R.add_reminder("", "in 1 hour", home=home) is None
    assert R.add_reminder("something", "nonsense when", home=home) is None


def test_due_only_when_time_passed(home) -> None:
    R.add_reminder("future", "in 2 hours", home=home)
    past = R.add_reminder("past", time.time() - 60, home=home)
    due = R.due_reminders(home=home)
    assert [d.id for d in due] == [past.id]


def test_take_due_deliveries_claims_once(home) -> None:
    R.add_reminder("ping", time.time() - 5, home=home)
    first = R.take_due_deliveries(home=home)
    assert len(first) == 1
    # second drain returns nothing — never delivered twice
    assert R.take_due_deliveries(home=home) == []
    assert R.list_reminders(status=R.STATUS_FIRED, home=home)


def test_recurring_rolls_forward_and_stays_pending(home) -> None:
    R.add_reminder("daily meds", time.time() - 5, recurrence="daily", home=home)
    claimed = R.take_due_deliveries(home=home)
    assert len(claimed) == 1
    live = R.list_reminders(home=home)
    assert live[0].status == R.STATUS_PENDING
    assert live[0].due_ts > time.time()  # rolled into the future


def test_recurring_skips_missed_occurrences(home) -> None:
    """Machine asleep for a week: fire once, not seven times."""
    long_ago = time.time() - 5 * 86400
    R.add_reminder("daily", long_ago, recurrence="daily", home=home)
    claimed = R.take_due_deliveries(home=home)
    assert len(claimed) == 1
    nxt = R.list_reminders(home=home)[0]
    assert nxt.due_ts > time.time()


def test_complete_closes_oneshot_but_rolls_recurring(home) -> None:
    one = R.add_reminder("one shot", "in 1 hour", home=home)
    rec = R.add_reminder("weekly", "in 1 hour", recurrence="weekly", home=home)
    assert R.complete_reminder(one.id, home=home).status == R.STATUS_DONE
    rolled = R.complete_reminder(rec.id, home=home)
    assert rolled.status == R.STATUS_PENDING
    assert rolled.due_ts > time.time() + 3600


def test_snooze_and_cancel(home) -> None:
    r = R.add_reminder("later", time.time() - 5, home=home)
    s = R.snooze_reminder(r.id, 30, home=home)
    assert s.status == R.STATUS_PENDING and s.due_ts > time.time()
    assert R.cancel_reminder(r.id, home=home).status == R.STATUS_CANCELLED
    assert R.due_reminders(home=home) == []


def test_mutations_on_missing_id_are_safe(home) -> None:
    assert R.complete_reminder("nope", home=home) is None
    assert R.snooze_reminder("nope", home=home) is None
    assert R.cancel_reminder("nope", home=home) is None


def test_source_ref_dedupes(home) -> None:
    a = R.add_reminder("Power bill due", "2026-09-01", source_ref="bill:1", home=home)
    b = R.add_reminder("Power bill due", "2026-09-01", source_ref="bill:1", home=home)
    assert a.id == b.id
    assert len(R.list_reminders(home=home)) == 1


def test_list_hides_done_by_default(home) -> None:
    r = R.add_reminder("x", "in 1 hour", home=home)
    R.complete_reminder(r.id, home=home)
    assert R.list_reminders(home=home) == []
    assert len(R.list_reminders(include_done=True, home=home)) == 1


# --- tick / thread ----------------------------------------------------------


def test_reminder_tick_calls_back(home) -> None:
    R.add_reminder("fire me", time.time() - 1, home=home)
    seen: list = []
    out = R.reminder_tick(home, on_due=lambda items: seen.append(items))
    assert len(out) == 1
    assert seen and seen[0][0].text == "fire me"


def test_tick_survives_bad_callback(home) -> None:
    R.add_reminder("boom", time.time() - 1, home=home)

    def bad(_items):
        raise RuntimeError("delivery exploded")

    # A failing delivery must not crash the clock.
    assert len(R.reminder_tick(home, on_due=bad)) == 1


def test_thread_starts_and_stops(home) -> None:
    import threading

    ev = threading.Event()
    t = R.start_reminder_thread(home, interval_s=5, stop_event=ev)
    assert t.is_alive()
    ev.set()
    t.join(timeout=6)


# --- seeding from bills -----------------------------------------------------


def test_sync_from_bills_creates_and_dedupes(home, monkeypatch) -> None:
    from types import SimpleNamespace

    soon = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    fake_bills = [
        SimpleNamespace(id="b1", name="Power", amount=88.40, cadence="monthly", next_due=soon),
        SimpleNamespace(id="b2", name="Water", amount=31.0, cadence="monthly", next_due=""),
    ]
    monkeypatch.setattr(
        "remedy.assistant.store.get_assistant_store",
        lambda h=None: SimpleNamespace(list_bills=lambda: fake_bills),
    )
    made = R.sync_from_bills(home=home)
    assert made == 1  # the one with a due date
    items = R.list_reminders(home=home)
    assert "Power due" in items[0].text and "88.40" in items[0].text
    assert items[0].recurrence == "monthly"
    # idempotent
    R.sync_from_bills(home=home)
    assert len(R.list_reminders(home=home)) == 1
