"""Reach — getting a due thing to the user, wherever they are.

Phase 2 of the real-life work. The clock (``core.reminders``) decides *when*;
this decides *whether it is worth interrupting*, and *where* to land:

- **Outbox** (``~/.remedy/notifications.json``) — durable, survives restarts, so
  the desktop can show what fired while it was closed. Nothing is ever dropped
  silently.
- **Messengers** — the gateway channels the owner already uses (Telegram,
  Signal, …) so a partner can reach them away from the desk.

Policy is deliberately conservative: a partner who pings too much gets muted.
Quiet hours **defer** rather than discard — a 3am reminder surfaces at 7am.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_lock = threading.RLock()

_IMPORTANCE_RANK = {"low": 0, "normal": 1, "high": 2}


def _home(home: str | Path | None = None) -> Path:
    base = home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    return Path(base).expanduser()


def _outbox_path(home: str | Path | None = None) -> Path:
    d = _home(home)
    with suppress(Exception):
        d.mkdir(parents=True, exist_ok=True)
    return d / "notifications.json"


@dataclass
class NotifyPolicy:
    """When it is acceptable to interrupt."""

    quiet_enabled: bool = True
    quiet_start_hour: int = 22  # 22:00
    quiet_end_hour: int = 7  # 07:00
    # Only this importance (or above) may break quiet hours.
    quiet_min_importance: str = "high"
    # Push to messenger channels as well as the desktop outbox.
    messengers_enabled: bool = True
    # Suppress an identical message repeated inside this window (seconds).
    dedupe_window_s: float = 300.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_policy(home: str | Path | None = None) -> NotifyPolicy:
    """Policy from config.toml ``[notifications]``; defaults are conservative."""
    pol = NotifyPolicy()
    with suppress(Exception):
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        raw = cfg.get("notifications")
        if isinstance(raw, dict):
            pol = NotifyPolicy(
                quiet_enabled=bool(raw.get("quiet_enabled", pol.quiet_enabled)),
                quiet_start_hour=int(raw.get("quiet_start_hour", pol.quiet_start_hour)),
                quiet_end_hour=int(raw.get("quiet_end_hour", pol.quiet_end_hour)),
                quiet_min_importance=str(
                    raw.get("quiet_min_importance", pol.quiet_min_importance)
                ),
                messengers_enabled=bool(
                    raw.get("messengers_enabled", pol.messengers_enabled)
                ),
                dedupe_window_s=float(raw.get("dedupe_window_s", pol.dedupe_window_s)),
            )
    return pol


def in_quiet_hours(policy: NotifyPolicy, now: float | None = None) -> bool:
    """True inside the owner's quiet window (handles the overnight wrap)."""
    if not policy.quiet_enabled:
        return False
    n = datetime.fromtimestamp(time.time() if now is None else now)
    start = int(policy.quiet_start_hour) % 24
    end = int(policy.quiet_end_hour) % 24
    h = n.hour
    if start == end:
        return False
    if start < end:  # e.g. 01:00–07:00
        return start <= h < end
    return h >= start or h < end  # overnight wrap, e.g. 22:00–07:00


def quiet_hours_end(policy: NotifyPolicy, now: float | None = None) -> float:
    """Epoch when the current quiet window ends (for deferral)."""
    n = datetime.fromtimestamp(time.time() if now is None else now)
    end = int(policy.quiet_end_hour) % 24
    candidate = n.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate.timestamp() <= n.timestamp():
        candidate = candidate + timedelta(days=1)
    return candidate.timestamp()


def decide(
    importance: str,
    policy: NotifyPolicy | None = None,
    *,
    now: float | None = None,
) -> str:
    """"deliver" or "defer" — never "drop". Quiet hours hold, they don't discard."""
    pol = policy or NotifyPolicy()
    if not in_quiet_hours(pol, now):
        return "deliver"
    rank = _IMPORTANCE_RANK.get((importance or "normal").lower(), 1)
    floor = _IMPORTANCE_RANK.get((pol.quiet_min_importance or "high").lower(), 2)
    return "deliver" if rank >= floor else "defer"


# --- durable outbox ---------------------------------------------------------


@dataclass
class Notification:
    id: str
    text: str
    created_ts: float = field(default_factory=time.time)
    source: str = "reminder"
    source_ref: str = ""
    importance: str = "normal"
    read: bool = False
    channels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Notification:
        raw = raw or {}
        return cls(
            id=str(raw.get("id") or f"n{int(time.time() * 1000):x}"),
            text=str(raw.get("text") or "")[:500],
            created_ts=float(raw.get("created_ts") or time.time()),
            source=str(raw.get("source") or "reminder"),
            source_ref=str(raw.get("source_ref") or "")[:200],
            importance=str(raw.get("importance") or "normal"),
            read=bool(raw.get("read")),
            channels=[str(c) for c in (raw.get("channels") or [])],
        )


def _read_outbox(home: str | Path | None) -> list[Notification]:
    p = _outbox_path(home)
    with suppress(Exception):
        raw = json.loads(p.read_text(encoding="utf-8"))
        return [Notification.from_dict(r) for r in (raw.get("notifications") or [])]
    return []


def _write_outbox(home: str | Path | None, items: list[Notification]) -> None:
    p = _outbox_path(home)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    # Keep the tail — an outbox is a feed, not an archive.
    payload = {"notifications": [n.to_dict() for n in items[-200:]]}
    with suppress(Exception):
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(p))
        return
    with suppress(Exception):
        tmp.unlink()


def push_notification(
    text: str,
    *,
    source: str = "reminder",
    source_ref: str = "",
    importance: str = "normal",
    channels: list[str] | None = None,
    dedupe_window_s: float = 300.0,
    home: str | Path | None = None,
    now: float | None = None,
) -> Notification | None:
    """Append to the outbox. Returns None when suppressed as a duplicate."""
    body = str(text or "").strip()
    if not body:
        return None
    n = time.time() if now is None else now
    with _lock:
        items = _read_outbox(home)
        win = max(0.0, float(dedupe_window_s or 0))
        if win:
            for ex in reversed(items[-40:]):
                if ex.text == body[:500] and (n - ex.created_ts) <= win:
                    return None
        note = Notification(
            id=f"n{int(n * 1000):x}{len(items) % 97:02x}",
            text=body[:500],
            created_ts=n,
            source=source or "reminder",
            source_ref=source_ref or "",
            importance=importance or "normal",
            channels=list(channels or []),
        )
        items.append(note)
        _write_outbox(home, items)
    return note


def list_notifications(
    *, unread_only: bool = False, limit: int = 50, home: str | Path | None = None
) -> list[Notification]:
    items = _read_outbox(home)
    if unread_only:
        items = [n for n in items if not n.read]
    items.sort(key=lambda n: n.created_ts, reverse=True)
    return items[: max(1, int(limit))]


def mark_read(
    ids: list[str] | None = None, *, all_: bool = False, home: str | Path | None = None
) -> int:
    want = {str(i) for i in (ids or [])}
    changed = 0
    with _lock:
        items = _read_outbox(home)
        for n in items:
            if not n.read and (all_ or n.id in want):
                n.read = True
                changed += 1
        if changed:
            _write_outbox(home, items)
    return changed


def unread_count(home: str | Path | None = None) -> int:
    return sum(1 for n in _read_outbox(home) if not n.read)


# --- the delivery pass ------------------------------------------------------


def format_reminder(text: str, *, importance: str = "normal") -> str:
    """One clear line — she is speaking, not a system daemon."""
    prefix = "Heads up" if importance == "high" else "Reminder"
    return f"{prefix}: {text}"


def deliver_due(
    *,
    home: str | Path | None = None,
    policy: NotifyPolicy | None = None,
    messenger_send=None,
    now: float | None = None,
) -> dict[str, Any]:
    """Full pass: defer what quiet hours should hold, deliver the rest.

    ``messenger_send`` is an optional ``fn(text) -> Any`` used to reach the owner
    away from the desk; failures never block the durable outbox.
    """
    from remedy.core import reminders as R

    pol = policy or load_policy(home)
    n = time.time() if now is None else now
    delivered: list[str] = []
    deferred: list[str] = []

    # Peek before claiming so a deferral simply moves the due time.
    for r in R.due_reminders(now=n, home=home):
        if decide(r.importance, pol, now=n) == "defer":
            until = quiet_hours_end(pol, n)
            minutes = max(1, int((until - n) / 60))
            R.snooze_reminder(r.id, minutes, home=home)
            deferred.append(r.id)

    claimed = R.take_due_deliveries(now=n, home=home)
    for r in claimed:
        text = format_reminder(r.text, importance=r.importance)
        note = push_notification(
            text,
            source=r.source or "reminder",
            source_ref=r.id,
            importance=r.importance,
            dedupe_window_s=pol.dedupe_window_s,
            home=home,
            now=n,
        )
        if note is None:
            continue  # duplicate inside the window
        delivered.append(r.id)
        if pol.messengers_enabled and messenger_send is not None:
            with suppress(Exception):
                messenger_send(text)
    return {
        "delivered": delivered,
        "deferred": deferred,
        "count": len(delivered),
    }


def start_delivery_thread(
    home: str | Path | None = None,
    *,
    interval_s: int = 30,
    messenger_send=None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """The clock + reach loop. Cheap enough to always run; never raises out."""
    ev = stop_event or threading.Event()

    def _beat() -> None:
        while not ev.is_set():
            with suppress(Exception):
                deliver_due(home=home, messenger_send=messenger_send)
            ev.wait(max(5, int(interval_s)))

    t = threading.Thread(target=_beat, name="remedy-notify", daemon=True)
    t._notify_stop = ev  # type: ignore[attr-defined]
    t.start()
    return t
