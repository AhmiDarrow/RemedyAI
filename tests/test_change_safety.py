"""Change-safety standing guidance for coding turns."""

from __future__ import annotations

from remedy.core.change_safety import CHANGE_SAFETY_SNIPPET, change_safety_block
from remedy.core.intent_policy import format_policy_block, policy_for_intent


def test_snippet_mentions_skill_and_smoke():
    assert "change-safety" in CHANGE_SAFETY_SNIPPET
    assert "blast radius" in CHANGE_SAFETY_SNIPPET.lower()
    assert change_safety_block() == CHANGE_SAFETY_SNIPPET
    assert change_safety_block(include=False) == ""


def test_tool_policy_includes_change_safety():
    # "implement" routes to the build pack (also change-safety tagged)
    pack = policy_for_intent("chat", user_text="please implement a fix for login")
    assert pack["id"] == "build"
    assert pack.get("change_safety") is True
    block = format_policy_block(pack)
    assert "Change-safety" in block or "change-safety" in block
    # Fix/debug wording → task loop (research → plan → build) with change-safety
    tool = policy_for_intent("chat", user_text="please fix the login bug")
    assert tool["id"] in ("task", "tool", "build")
    assert tool.get("change_safety") is True
    tool_block = format_policy_block(tool)
    assert "Change-safety" in tool_block or "tool" in tool_block.lower()


def test_autonomous_policy_includes_change_safety():
    pack = policy_for_intent("chat", user_text="handle this on your own end-to-end")
    assert pack["id"] == "autonomous"
    block = format_policy_block(pack)
    assert "change-safety" in block.lower() or "Change-safety" in block


def test_chat_policy_skips_change_safety():
    pack = policy_for_intent("chat", user_text="how are you today?")
    assert pack["id"] == "chat"
    assert not pack.get("change_safety")
    block = format_policy_block(pack)
    assert "Change-safety" not in block
