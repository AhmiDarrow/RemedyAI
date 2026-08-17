"""Reminder tools — Remedy's sense of *when*.

Real life is scheduled. These let her hold a time for the user and surface it
when it matters, instead of only knowing a deadline if asked.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Any

from remedy.core import reminders as R


def _home_of(runtime: Any) -> Any:
    with contextlib.suppress(Exception):
        return getattr(getattr(runtime, "config", None), "home_dir", None)
    return None


def _row(r: R.Reminder) -> dict[str, Any]:
    return {
        "id": r.id,
        "text": r.text,
        "when": r.when_human(),
        "due_iso": datetime.fromtimestamp(r.due_ts).isoformat(timespec="minutes"),
        "status": r.status,
        "recurrence": r.recurrence or "once",
        "importance": r.importance,
        "source": r.source,
    }


def register_reminder_tools(runtime: Any) -> None:
    home = _home_of(runtime)

    async def remind_me(
        text: str = "",
        when: str = "",
        recurrence: str = "",
        importance: str = "normal",
    ) -> str:
        """Hold a time for the user; Remedy surfaces it when it comes due."""
        if not str(text or "").strip():
            return json.dumps({"ok": False, "message": "What should I remind you about?"})
        if not str(when or "").strip():
            return json.dumps(
                {
                    "ok": False,
                    "message": "When? e.g. 'in 30m', 'tomorrow 9am', 'friday', '2026-09-01'.",
                }
            )
        r = R.add_reminder(
            text,
            when,
            recurrence=recurrence,
            importance=importance,
            source="user",
            home=home,
        )
        if r is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": (
                        f"I couldn't read {when!r} as a time. Try 'in 2 hours', "
                        "'tomorrow 9am', 'friday 3pm', or an ISO date."
                    ),
                }
            )
        return json.dumps(
            {
                "ok": True,
                "reminder": _row(r),
                "message": f"Holding that for {r.when_human()}"
                + (f", repeating {r.recurrence}" if r.recurrence else ""),
            },
            indent=2,
        )

    async def reminder_list(include_done: bool = False, limit: int = 30) -> str:
        """What Remedy is holding — soonest first."""
        items = R.list_reminders(
            include_done=bool(include_done), limit=int(limit or 30), home=home
        )
        return json.dumps(
            {
                "ok": True,
                "count": len(items),
                "reminders": [_row(r) for r in items],
                "message": f"{len(items)} reminder(s)",
            },
            indent=2,
        )

    async def reminder_done(reminder_id: str = "") -> str:
        """Mark handled (a repeating one rolls to its next date)."""
        r = R.complete_reminder(reminder_id, home=home)
        if r is None:
            return json.dumps({"ok": False, "message": f"No reminder {reminder_id!r}"})
        nxt = (
            f" Next: {r.when_human()}." if r.status == R.STATUS_PENDING else ""
        )
        return json.dumps(
            {"ok": True, "reminder": _row(r), "message": f"Done.{nxt}"}, indent=2
        )

    async def reminder_snooze(reminder_id: str = "", minutes: int = 15) -> str:
        """Push a reminder out by N minutes."""
        r = R.snooze_reminder(reminder_id, int(minutes or 15), home=home)
        if r is None:
            return json.dumps({"ok": False, "message": f"No reminder {reminder_id!r}"})
        return json.dumps(
            {"ok": True, "reminder": _row(r), "message": f"Snoozed to {r.when_human()}"},
            indent=2,
        )

    async def reminder_cancel(reminder_id: str = "") -> str:
        """Drop a reminder entirely."""
        r = R.cancel_reminder(reminder_id, home=home)
        if r is None:
            return json.dumps({"ok": False, "message": f"No reminder {reminder_id!r}"})
        return json.dumps({"ok": True, "message": f"Cancelled: {r.text}"}, indent=2)

    async def reminder_sync_bills() -> str:
        """Turn stored bills with due dates into reminders (idempotent)."""
        n = R.sync_from_bills(home=home)
        return json.dumps(
            {
                "ok": True,
                "created": n,
                "message": (
                    f"{n} bill reminder(s) set up"
                    if n
                    else "No new bill due-dates to track"
                ),
            },
            indent=2,
        )

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "remind_me",
        "Hold a time for the user and surface it when due. when: 'in 30m', "
        "'tomorrow 9am', 'friday 3pm', 'YYYY-MM-DD', or ISO. recurrence: "
        "daily|weekly|monthly|yearly for repeats.",
        remind_me,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to surface"},
                "when": {"type": "string", "description": "When it is due"},
                "recurrence": {
                    "type": "string",
                    "description": "daily | weekly | monthly | yearly (omit for one-off)",
                },
                "importance": {"type": "string", "description": "low | normal | high"},
            },
            "required": ["text", "when"],
        },
    )
    reg.register_builtin_handler(
        "reminder_list",
        "List reminders Remedy is holding (soonest first).",
        reminder_list,
        {
            "type": "object",
            "properties": {
                "include_done": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
        },
    )
    reg.register_builtin_handler(
        "reminder_done",
        "Mark a reminder handled (repeating ones roll to the next date).",
        reminder_done,
        {
            "type": "object",
            "properties": {"reminder_id": {"type": "string"}},
            "required": ["reminder_id"],
        },
    )
    reg.register_builtin_handler(
        "reminder_snooze",
        "Push a reminder out by N minutes.",
        reminder_snooze,
        {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string"},
                "minutes": {"type": "integer"},
            },
            "required": ["reminder_id"],
        },
    )
    reg.register_builtin_handler(
        "reminder_cancel",
        "Cancel a reminder entirely.",
        reminder_cancel,
        {
            "type": "object",
            "properties": {"reminder_id": {"type": "string"}},
            "required": ["reminder_id"],
        },
    )
    reg.register_builtin_handler(
        "reminder_sync_bills",
        "Create reminders from stored bills that have due dates (safe to re-run).",
        reminder_sync_bills,
        {"type": "object", "properties": {}},
    )
