"""Remedy's clock — durable reminders that actually fire.

Real life is scheduled: bills come due, prescriptions run out, registrations
expire, someone needs picking up. Remedy already stores due dates (bills
``next_due``, life-goal ``next_by``) but nothing ever woke on them, so she could
only mention a deadline if you happened to ask.

This is the store + the tick. Delivery (desktop / messenger) drains
``take_due_deliveries``; the store itself never talks to a network.

On disk: ``~/.remedy/reminders.json`` — atomic write under a process lock, same
idiom as the build ledger.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_lock = threading.RLock()

STATUS_PENDING = "pending"
STATUS_FIRED = "fired"  # delivered, awaiting done/dismiss (one-shot only)
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

RECURRENCES = ("", "daily", "weekly", "monthly", "yearly")
IMPORTANCES = ("low", "normal", "high")


def _home(home: str | Path | None = None) -> Path:
    base = home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    return Path(base).expanduser()


def _store_path(home: str | Path | None = None) -> Path:
    d = _home(home)
    with suppress(Exception):
        d.mkdir(parents=True, exist_ok=True)
    return d / "reminders.json"


def _new_id() -> str:
    return f"r{int(time.time() * 1000):x}{os.getpid() % 997:03x}"


@dataclass
class Reminder:
    """One thing to surface at a time."""

    id: str
    text: str
    due_ts: float
    created_ts: float = field(default_factory=time.time)
    status: str = STATUS_PENDING
    recurrence: str = ""
    importance: str = "normal"
    # Where it came from: user | bill | goal | calendar | document
    source: str = "user"
    source_ref: str = ""
    fired_ts: float = 0.0
    fire_count: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Reminder":
        raw = raw or {}
        return cls(
            id=str(raw.get("id") or _new_id()),
            text=str(raw.get("text") or "")[:400],
            due_ts=float(raw.get("due_ts") or 0.0),
            created_ts=float(raw.get("created_ts") or time.time()),
            status=str(raw.get("status") or STATUS_PENDING),
            recurrence=str(raw.get("recurrence") or ""),
            importance=str(raw.get("importance") or "normal"),
            source=str(raw.get("source") or "user"),
            source_ref=str(raw.get("source_ref") or "")[:200],
            fired_ts=float(raw.get("fired_ts") or 0.0),
            fire_count=int(raw.get("fire_count") or 0),
            note=str(raw.get("note") or "")[:400],
        )

    def is_due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.status == STATUS_PENDING and self.due_ts <= now

    def when_human(self) -> str:
        with suppress(Exception):
            return datetime.fromtimestamp(self.due_ts).strftime("%a %d %b %H:%M")
        return str(self.due_ts)


# --- natural "when" parsing ------------------------------------------------

_REL_RE = re.compile(
    r"(?i)^\s*in\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)\s*$"
)
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_TIME_RE = re.compile(r"(?i)\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")


def _apply_time(base: datetime, text: str, *, default_hour: int = 9) -> datetime:
    m = _TIME_RE.search(text or "")
    hour, minute = default_hour, 0
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        hour = max(0, min(hour, 23))
        minute = max(0, min(minute, 59))
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_when(raw: str, *, now: float | None = None) -> float | None:
    """Turn a human/ISO 'when' into an epoch timestamp (local time). None if unparsable.

    Accepts: ISO datetime, ``YYYY-MM-DD``, ``in 30m`` / ``in 2 hours`` /
    ``in 3 days``, ``today 5pm``, ``tomorrow`` (+optional time), a weekday name
    (+optional time), or a bare ``HH:MM`` / ``5pm`` today-or-tomorrow.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    base = datetime.fromtimestamp(time.time() if now is None else now)

    # epoch passed straight through
    with suppress(ValueError):
        v = float(s)
        if v > 1_000_000_000:  # plausible epoch seconds
            return v

    # ISO first (most precise)
    with suppress(ValueError):
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            return dt.timestamp()
        # date-only → default morning
        if len(s) <= 10:
            dt = dt.replace(hour=9, minute=0)
        return dt.timestamp()

    m = _REL_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("m"):
            delta = timedelta(minutes=n)
        elif unit.startswith("h"):
            delta = timedelta(hours=n)
        elif unit.startswith("d"):
            delta = timedelta(days=n)
        else:
            delta = timedelta(weeks=n)
        return (base + delta).timestamp()

    low = s.lower()
    if low.startswith("tomorrow"):
        return _apply_time(base + timedelta(days=1), s).timestamp()
    if low.startswith("today") or low.startswith("tonight"):
        d = _apply_time(base, s, default_hour=19 if "tonight" in low else 9)
        return d.timestamp()
    for name, idx in _WEEKDAYS.items():
        if re.search(rf"(?i)\b{name}\b", low):
            ahead = (idx - base.weekday()) % 7
            if ahead == 0:
                ahead = 7  # "friday" on a Friday means next Friday
            return _apply_time(base + timedelta(days=ahead), s).timestamp()
    # bare time → today if still ahead, else tomorrow
    if _TIME_RE.search(low):
        cand = _apply_time(base, s)
        if cand.timestamp() <= base.timestamp():
            cand = cand + timedelta(days=1)
        return cand.timestamp()
    return None


def next_occurrence(due_ts: float, recurrence: str) -> float | None:
    """Advance a recurring due date past ``due_ts``. None when not recurring."""
    rec = (recurrence or "").strip().lower()
    if rec not in RECURRENCES or not rec:
        return None
    dt = datetime.fromtimestamp(due_ts)
    if rec == "daily":
        return (dt + timedelta(days=1)).timestamp()
    if rec == "weekly":
        return (dt + timedelta(weeks=1)).timestamp()
    if rec == "monthly":
        month = dt.month + 1
        year = dt.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        day = min(dt.day, _days_in_month(year, month))
        return dt.replace(year=year, month=month, day=day).timestamp()
    if rec == "yearly":
        year = dt.year + 1
        day = min(dt.day, _days_in_month(year, dt.month))
        return dt.replace(year=year, day=day).timestamp()
    return None


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


# --- store -----------------------------------------------------------------


def _read(home: str | Path | None) -> list[Reminder]:
    p = _store_path(home)
    with suppress(Exception):
        raw = json.loads(p.read_text(encoding="utf-8"))
        return [Reminder.from_dict(r) for r in (raw.get("reminders") or [])]
    return []


def _write(home: str | Path | None, items: list[Reminder]) -> None:
    p = _store_path(home)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    payload = {"reminders": [r.to_dict() for r in items]}
    with suppress(Exception):
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(p))
        return
    with suppress(Exception):
        tmp.unlink()


def add_reminder(
    text: str,
    when: str | float,
    *,
    recurrence: str = "",
    importance: str = "normal",
    source: str = "user",
    source_ref: str = "",
    note: str = "",
    home: str | Path | None = None,
) -> Reminder | None:
    """Create a reminder. ``when`` accepts ISO / natural text / epoch."""
    body = str(text or "").strip()
    if not body:
        return None
    due = when if isinstance(when, (int, float)) else parse_when(str(when))
    if not due:
        return None
    rec = (recurrence or "").strip().lower()
    if rec not in RECURRENCES:
        rec = ""
    imp = (importance or "normal").strip().lower()
    if imp not in IMPORTANCES:
        imp = "normal"
    r = Reminder(
        id=_new_id(),
        text=body[:400],
        due_ts=float(due),
        recurrence=rec,
        importance=imp,
        source=source or "user",
        source_ref=source_ref or "",
        note=note or "",
    )
    with _lock:
        items = _read(home)
        # De-dupe: same source_ref + same due minute is the same thing.
        if r.source_ref:
            for ex in items:
                if (
                    ex.source_ref == r.source_ref
                    and ex.status == STATUS_PENDING
                    and abs(ex.due_ts - r.due_ts) < 60
                ):
                    return ex
        items.append(r)
        _write(home, items)
    return r


def list_reminders(
    *,
    status: str = "",
    include_done: bool = False,
    limit: int = 100,
    home: str | Path | None = None,
) -> list[Reminder]:
    items = _read(home)
    if status:
        items = [r for r in items if r.status == status]
    elif not include_done:
        items = [r for r in items if r.status in (STATUS_PENDING, STATUS_FIRED)]
    items.sort(key=lambda r: r.due_ts)
    return items[: max(1, int(limit))]


def due_reminders(
    *, now: float | None = None, home: str | Path | None = None
) -> list[Reminder]:
    n = time.time() if now is None else now
    return [r for r in _read(home) if r.is_due(n)]


def _mutate(
    reminder_id: str, home: str | Path | None, fn
) -> Reminder | None:
    rid = str(reminder_id or "").strip()
    if not rid:
        return None
    with _lock:
        items = _read(home)
        hit = None
        for r in items:
            if r.id == rid:
                hit = r
                break
        if hit is None:
            return None
        fn(hit)
        _write(home, items)
        return hit


def complete_reminder(
    reminder_id: str, *, home: str | Path | None = None
) -> Reminder | None:
    """Mark done. A recurring reminder rolls forward instead of closing."""

    def _done(r: Reminder) -> None:
        nxt = next_occurrence(r.due_ts, r.recurrence)
        if nxt:
            r.due_ts = nxt
            r.status = STATUS_PENDING
            r.fired_ts = 0.0
        else:
            r.status = STATUS_DONE

    return _mutate(reminder_id, home, _done)


def cancel_reminder(
    reminder_id: str, *, home: str | Path | None = None
) -> Reminder | None:
    return _mutate(reminder_id, home, lambda r: setattr(r, "status", STATUS_CANCELLED))


def snooze_reminder(
    reminder_id: str, minutes: int = 15, *, home: str | Path | None = None
) -> Reminder | None:
    mins = max(1, min(int(minutes or 15), 60 * 24 * 14))

    def _snooze(r: Reminder) -> None:
        r.due_ts = time.time() + mins * 60
        r.status = STATUS_PENDING

    return _mutate(reminder_id, home, _snooze)


def mark_fired(
    reminder_id: str, *, now: float | None = None, home: str | Path | None = None
) -> Reminder | None:
    """Record delivery. Recurring rolls to the next occurrence and stays pending."""
    n = time.time() if now is None else now

    def _fire(r: Reminder) -> None:
        r.fired_ts = n
        r.fire_count = int(r.fire_count or 0) + 1
        nxt = next_occurrence(r.due_ts, r.recurrence)
        if nxt:
            # Skip any occurrences already in the past (machine was asleep).
            while nxt and nxt <= n:
                nxt = next_occurrence(nxt, r.recurrence)
            r.due_ts = float(nxt or (n + 86400))
            r.status = STATUS_PENDING
        else:
            r.status = STATUS_FIRED

    return _mutate(reminder_id, home, _fire)


def take_due_deliveries(
    *, now: float | None = None, home: str | Path | None = None
) -> list[Reminder]:
    """Atomically claim everything due — marks fired so it is delivered once.

    Returns snapshots of what was claimed; the caller owns delivery (Phase 2).
    """
    n = time.time() if now is None else now
    claimed: list[Reminder] = []
    with _lock:
        items = _read(home)
        changed = False
        for r in items:
            if not r.is_due(n):
                continue
            snapshot = Reminder.from_dict(r.to_dict())
            claimed.append(snapshot)
            r.fired_ts = n
            r.fire_count = int(r.fire_count or 0) + 1
            nxt = next_occurrence(r.due_ts, r.recurrence)
            if nxt:
                while nxt and nxt <= n:
                    nxt = next_occurrence(nxt, r.recurrence)
                r.due_ts = float(nxt or (n + 86400))
                r.status = STATUS_PENDING
            else:
                r.status = STATUS_FIRED
            changed = True
        if changed:
            _write(home, items)
    return claimed


# --- seeding from what Remedy already knows --------------------------------


def sync_from_bills(*, home: str | Path | None = None) -> int:
    """Create reminders for stored bills that have a ``next_due`` date.

    She already tracks these; nothing ever surfaced them. Idempotent via
    source_ref de-dupe.
    """
    made = 0
    with suppress(Exception):
        from remedy.assistant.store import get_assistant_store

        store = get_assistant_store(home)
        for b in store.list_bills():
            due_raw = str(getattr(b, "next_due", "") or "").strip()
            if not due_raw:
                continue
            due = parse_when(due_raw)
            if not due:
                continue
            name = str(getattr(b, "name", "") or "bill")
            amount = float(getattr(b, "amount", 0) or 0)
            cadence = str(getattr(b, "cadence", "") or "").lower()
            rec = cadence if cadence in RECURRENCES else ""
            r = add_reminder(
                f"{name} due" + (f" (${amount:.2f})" if amount else ""),
                due,
                recurrence=rec,
                source="bill",
                source_ref=f"bill:{getattr(b, 'id', name)}",
                home=home,
            )
            if r is not None:
                made += 1
    return made


# --- scheduler thread ------------------------------------------------------


def reminder_tick(
    home: str | Path | None = None,
    *,
    on_due=None,
) -> list[Reminder]:
    """One scheduler pass: claim due reminders and hand them to ``on_due``."""
    due = take_due_deliveries(home=home)
    if due and on_due is not None:
        with suppress(Exception):
            on_due(due)
    return due


def start_reminder_thread(
    home: str | Path | None = None,
    *,
    interval_s: int = 30,
    on_due=None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Daemon clock. Cheap (a small JSON read), so it can always run."""
    ev = stop_event or threading.Event()

    def _beat() -> None:
        while not ev.is_set():
            with suppress(Exception):
                reminder_tick(home, on_due=on_due)
            ev.wait(max(5, int(interval_s)))

    t = threading.Thread(target=_beat, name="remedy-reminders", daemon=True)
    t._reminder_stop = ev  # type: ignore[attr-defined]
    t.start()
    return t
