"""Default task behavior is tools-until-done (the machine owns the schedule)."""

from __future__ import annotations

from remedy.core.build_engine import looks_like_build_request, looks_like_task_request
from remedy.core.intent_policy import policy_for_intent
from remedy.core.react_policy import _DEFAULT_SYSTEM_BODY


def test_system_body_says_tools_until_done_not_a_syllabus():
    body = _DEFAULT_SYSTEM_BODY
    assert "tools first" in body.lower()
    assert "do not write a plan and stop" in body.lower()
    assert "RESEARCH → PLAN → BUILD" not in body
    assert "1) **RESEARCH**" not in body


def test_task_policy_pack_for_generic_work():
    pack = policy_for_intent("chat", user_text="please review and fix the login bug")
    assert pack["id"] in ("task", "tool", "build")
    sys = pack.get("system") or ""
    assert "tool" in sys.lower()
    assert pack.get("prefer_tools") is True


def test_build_policy_includes_rpb():
    pack = policy_for_intent("chat", user_text="implement a calculator app")
    assert pack["id"] == "build"
    assert "implement" in (pack.get("system") or "").lower()


def test_pure_chat_stays_chat():
    pack = policy_for_intent("chat", user_text="how are you today?")
    assert pack["id"] == "chat"
    assert not pack.get("prefer_tools")


def test_task_alias_matches_build_detector():
    assert looks_like_task_request is looks_like_build_request
    assert looks_like_task_request("create a todo app with tests")
