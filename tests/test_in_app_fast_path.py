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


def test_todo_list_dir_verify_clipboard_which_goals():
    p = match_in_app_fast_path("show todos")
    assert p is not None and p.tool == "todo_read"
    p2 = match_in_app_fast_path("list files")
    assert p2 is not None and p2.tool == "list_dir"
    assert p2.arguments.get("path") == "."
    assert match_in_app_fast_path("list files in src") is None
    p3 = match_in_app_fast_path("run the tests")
    assert p3 is not None and p3.tool == "job_run"
    assert p3.arguments.get("kind") == "verify"
    p4 = match_in_app_fast_path("what's on the clipboard")
    assert p4 is not None and p4.tool == "clipboard_read"
    p5 = match_in_app_fast_path("which python")
    assert p5 is not None and p5.tool == "host_which"
    assert p5.arguments.get("name") == "python"
    p6 = match_in_app_fast_path("list my goals")
    assert p6 is not None and p6.tool == "goal_list"
    p7 = match_in_app_fast_path("ship status")
    assert p7 is not None and p7.tool == "ship_status"
    p8 = match_in_app_fast_path("mission status")
    assert p8 is not None and p8.tool == "mission_status"
    p9 = match_in_app_fast_path("what's on my screen")
    assert p9 is not None and p9.tool == "companion_context"
    p10 = match_in_app_fast_path("rmb status")
    assert p10 is not None and p10.tool == "rmb"
    assert p10.arguments.get("action") == "status"
    p11 = match_in_app_fast_path("list local ggufs")
    assert p11 is not None and p11.arguments.get("action") == "models"
    p12 = match_in_app_fast_path("what windows are open")
    assert p12 is not None and p12.tool == "computer_windows"
    assert p12.arguments.get("mode") == "list"
    assert match_in_app_fast_path("look at this") is None
    assert match_in_app_fast_path("look at my screen") is None
    assert match_in_app_fast_path("what changed") is None
    assert match_in_app_fast_path("what's changed") is None
    assert match_in_app_fast_path("show me what changed") is None
    assert match_in_app_fast_path("recall the login flow") is None
    git_changed = match_in_app_fast_path("what changed in git")
    assert git_changed is not None and git_changed.tool == "git_status"
    assert match_in_app_fast_path("what's the mission") is None
    assert match_in_app_fast_path("start rmb") is None
    assert match_in_app_fast_path("close the window") is None


def test_vault_help_hive_soul_skills_memory_mail_screenshot():
    p = match_in_app_fast_path("list vault")
    assert p is not None and p.tool == "vault_list"
    p = match_in_app_fast_path("help topics")
    assert p is not None and p.tool == "help_list"
    p = match_in_app_fast_path("help_read 20-rmb-local-agent")
    assert p is not None and p.tool == "help_read"
    assert p.arguments.get("id") == "20-rmb-local-agent"
    p = match_in_app_fast_path("hive status")
    assert p is not None and p.tool == "hive_status"
    p = match_in_app_fast_path("soul status")
    assert p is not None and p.tool == "soul_status"
    p = match_in_app_fast_path("reload skills")
    assert p is not None and p.tool == "skill_reload"
    p = match_in_app_fast_path("search skills for git")
    assert p is not None and p.tool == "skill_search"
    assert "git" in (p.arguments.get("query") or "")
    p = match_in_app_fast_path("what do you remember about oat milk")
    assert p is not None and p.tool == "memory_search"
    assert "oat milk" in p.arguments.get("query", "")
    p = match_in_app_fast_path("remember that I like oat milk")
    assert p is not None and p.tool == "memory_save"
    assert "oat milk" in p.arguments.get("content", "")
    p = match_in_app_fast_path("remember oat milk")
    assert p is not None and p.tool == "memory_save"
    assert match_in_app_fast_path("remember to buy milk") is None
    p = match_in_app_fast_path("is mail connected")
    assert p is not None and p.tool == "mail_status"
    p = match_in_app_fast_path("take a screenshot")
    assert p is not None and p.tool == "computer_screenshot"
    p = match_in_app_fast_path("list monitors")
    assert p is not None and p.tool == "computer_monitors"


def test_mutates_and_multi_step_stay_on_the_provider():
    assert match_in_app_fast_path("git commit") is None
    assert match_in_app_fast_path("git push origin") is None
    assert match_in_app_fast_path("git status and then commit") is None
    assert match_in_app_fast_path("show the diff then implement the fix") is None
    assert match_in_app_fast_path("run the tests and then implement the fix") is None
    assert match_in_app_fast_path("where is the bathroom") is None
    assert match_in_app_fast_path("") is None


def test_format_git_is_plain_text():
    out = format_in_app_fast_path_reply(
        "git_status", "**git_status** exit=0\n## master"
    )
    assert "master" in out


def test_format_vault_handles_only():
    out = format_in_app_fast_path_reply(
        "vault_list",
        json.dumps(
            {
                "ok": True,
                "items": [
                    {"handle": "card-visa", "kind": "card", "label": "Visa"},
                ],
            }
        ),
    )
    assert "card-visa" in out
    assert "Visa" in out
    assert "4111" not in out


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


def test_cwd_is_l0_not_a_provider_round():
    assert classify_turn_tier("pwd") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what's the project path") == TurnTier.L0_INSTANT
    from remedy.core.metabolism.l0 import try_l0_system_reply

    class _R:
        def effective_project_path(self):
            return "C:/proj"

    out = try_l0_system_reply(_R(), "pwd", preclassified=True)
    assert out is not None
    assert "C:/proj" in out
    assert "no provider" in out.lower()
