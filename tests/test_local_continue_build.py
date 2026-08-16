"""Keep-going / continue must force tools and reject intent monologues."""

from __future__ import annotations

from remedy.core.local_agent_optimize import (
    force_tool_choice_required,
    looks_like_intent_monologue,
    looks_like_tutorial_monologue,
    message_wants_build_work,
    message_wants_continue_work,
    message_wants_implement,
)


def test_keep_going_is_implement():
    assert message_wants_continue_work("keep going")
    assert message_wants_implement("keep going")
    assert message_wants_implement("Continue")
    assert message_wants_implement("resume the build")


def test_intent_monologue_from_screenshot():
    text = (
        "I'll build RemedyPDF — a cross-platform PDF viewer/editor with sleek UI, "
        "auto-update, and Windows/Android packaging. Let me start by laying out "
        "the architecture and creating the core project structure."
    )
    assert looks_like_intent_monologue(text)
    assert looks_like_tutorial_monologue(text)


def test_force_tools_on_keep_going_rmb():
    assert force_tool_choice_required(
        provider="rmb",
        model="Qwopus3.5-9B-Coder-MTP-Q4_K_M",
        base_url="http://127.0.0.1:8787/v1",
        tools=[{"type": "function", "function": {"name": "file_write"}}],
        user_message="keep going",
        step_index=0,
    )
    assert force_tool_choice_required(
        provider="rmb",
        model="x",
        base_url="http://127.0.0.1:8787/v1",
        tools=[{"type": "function"}],
        user_message="keep going",
        step_index=5,
    )


def test_history_unfinished_build_short_ok():
    history = [
        {
            "role": "assistant",
            "content": "I'll build RemedyPDF. Let me start by laying out the architecture.",
        }
    ]
    assert message_wants_build_work("ok", history)
