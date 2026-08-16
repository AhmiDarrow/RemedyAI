"""PA fast path — high-confidence tool-only matches (no provider)."""

from __future__ import annotations

import json

from remedy.assistant.fast_path import (
    format_fast_path_reply,
    match_assistant_fast_path,
)


def test_match_calendar_list():
    p = match_assistant_fast_path("What's on my calendar this week?")
    assert p is not None
    assert p.tool == "calendar_list_events"
    assert p.arguments.get("days") == 7

    p2 = match_assistant_fast_path("show my calendar today")
    assert p2 is not None
    assert p2.arguments.get("days") == 1


def test_match_brief_and_budget():
    assert match_assistant_fast_path("daily brief").tool == "assistant_brief"
    assert match_assistant_fast_path("budget status").tool == "budget_status"
    assert match_assistant_fast_path("list my debts").tool == "debt_list"
    assert match_assistant_fast_path("list bills").tool == "bill_list"


def test_match_mail_inbox():
    p = match_assistant_fast_path("check my email")
    assert p is not None
    assert p.tool == "mail_list"
    assert match_assistant_fast_path("what's in my inbox").tool == "mail_list"


def test_match_expense_log():
    p = match_assistant_fast_path("log $50 to groceries")
    assert p is not None
    assert p.tool == "budget_tx_add"
    assert p.arguments["amount"] == 50.0
    assert "grocer" in p.arguments["category"].lower()

    p2 = match_assistant_fast_path("spent 12.5 on coffee")
    assert p2 is not None
    assert p2.arguments["amount"] == 12.5


def test_reject_complex_and_long():
    assert match_assistant_fast_path("") is None
    assert (
        match_assistant_fast_path(
            "what's on my calendar and then write a poem about it"
        )
        is None
    )
    assert (
        match_assistant_fast_path(
            "check my budget and also implement a new feature in the app"
        )
        is None
    )
    assert match_assistant_fast_path("should I pay off my debt first?") is None
    # Multi-sentence
    assert match_assistant_fast_path("Show budget. Then plan my week.") is None


def test_format_calendar_and_budget():
    cal = format_fast_path_reply(
        "calendar_list_events",
        json.dumps(
            {
                "ok": True,
                "count": 1,
                "events": [
                    {"start": "2026-08-01T09:00:00Z", "title": "Standup"},
                ],
            }
        ),
    )
    assert "Standup" in cal
    assert "Calendar" in cal

    fail = format_fast_path_reply(
        "calendar_list_events",
        json.dumps({"ok": False, "message": "Google not connected"}),
    )
    assert "not connected" in fail.lower()

    bud = format_fast_path_reply(
        "budget_status",
        json.dumps(
            {
                "ok": True,
                "label": "2026-07",
                "categories": [
                    {
                        "category": "food",
                        "spent": 10,
                        "planned": 100,
                        "remaining": 90,
                    }
                ],
                "disclaimer": "not advice",
            }
        ),
    )
    assert "food" in bud
    assert "90" in bud
