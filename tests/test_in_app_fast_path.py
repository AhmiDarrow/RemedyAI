"""In-app fast path — git / reminders skip the provider."""

from __future__ import annotations

import json

from remedy.core.in_app_fast_path import (
    format_in_app_fast_path_reply,
    match_in_app_fast_path,
)
from remedy.core.metabolism.tier import TurnTier, classify_turn_tier


def test_git_status_diff_log_match():
    p = match_in_app_fast_path("git status")
    assert p is not None and p.tool == "git_status"
    p2 = match_in_app_fast_path("what's the diff")
    assert p2 is not None and p2.tool == "git_diff"
    p3 = match_in_app_fast_path("recent commits")
    assert p3 is not None and p3.tool == "git_log"
    assert p3.arguments.get("limit") == 20


def test_reminder_list_and_remind_me():
    p = match_in_app_fast_path("list my reminders")
    assert p is not None and p.tool == "reminder_list"
    p2 = match_in_app_fast_path("remind me in 30m to take out the trash")
    assert p2 is not None and p2.tool == "remind_me"
    assert p2.arguments["when"] == "in 30m"
    assert "trash" in p2.arguments["text"]
    p3 = match_in_app_fast_path("remind me tomorrow to call the pharmacy")
    assert p3 is not None
    assert p3.arguments["when"] == "tomorrow"


def test_mutates_and_multi_step_stay_on_the_provider():
    assert match_in_app_fast_path("git commit") is None
    assert match_in_app_fast_path("git push origin") is None
    assert match_in_app_fast_path("git status and then commit") is None
    assert match_in_app_fast_path("show the diff then implement the fix") is None
    assert match_in_app_fast_path("") is None


def test_format_git_is_plain_text():
    out = format_in_app_fast_path_reply(
        "git_status", "**git_status** exit=0\n## master"
    )
    assert "master" in out


def test_format_reminder_list():
    out = format_in_app_fast_path_reply(
        "reminder_list",
        json.dumps(
            {
                "ok": True,
                "count": 1,
                "reminders": [{"when": "in 30m", "text": "pharmacy"}],
            }
        ),
    )
    assert "pharmacy" in out
    assert "Reminders" in out


def test_clock_is_l0_not_a_provider_round():
    assert classify_turn_tier("what time is it") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what's the date") == TurnTier.L0_INSTANT
    from remedy.core.metabolism.l0 import try_l0_system_reply

    class _R:
        pass

    out = try_l0_system_reply(_R(), "what time is it", preclassified=True)
    assert out is not None
    assert "Local time" in out
    assert "no provider" in out.lower()
