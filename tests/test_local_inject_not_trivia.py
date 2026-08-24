"""Remedy's own nudges must never be mistaken for the owner's message.

The local body optimizer strips tools when the user's turn is "pure trivia"
(``1 + 1``, a pasted blob of tool markup). It used to read *the last user
message in the list* — but Remedy injects its own bracket-tagged turns to steer
a local model, and those nudges quote tool names:

    [Partner · PROJECT TREE - tools required]
    Reply with native tool_calls only (file_read / file_write / ...)

``looks_like_injected_tool_markup`` reads that as pasted markup, so the step was
classified trivia and every tool was stripped from the very request whose nudge
demanded a tool call. A local model then deadlocks: no tools, so no tool call,
so another nudge, until the step wall ends the turn. Observed live as a local
model making zero tool calls across eleven steps.

These tests pin the boundary: injects are recognised as scaffolding, the real
user turn is what gets classified, and a tool-bearing local request keeps its
tools.
"""

from __future__ import annotations

from remedy.core.local_agent_optimize import (
    apply_local_body_optimize,
    is_harness_inject,
    last_real_user_message,
)
from remedy.core.react_policy import is_pure_trivia_message

LOCAL = {
    "provider": "rmb",
    "model": "gemma-4-12b",
    "base_url": "http://127.0.0.1:8787/v1",
}

PROJECT_TREE_NUDGE = (
    "[Partner \u00b7 PROJECT TREE - tools required]\n"
    "Project root: C:\\tmp\\workspace\n\n"
    "Reply with native tool_calls only (file_read / file_write / list_dir)."
)


def _tools(*names: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": n,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def test_project_tree_nudge_is_recognised_as_harness_inject() -> None:
    assert is_harness_inject(PROJECT_TREE_NUDGE)
    assert is_harness_inject("[Task loop \u2014 PLAN] Research received.")
    assert is_harness_inject("[Local create \u00b7 tools required] Your next reply MUST")


def test_ordinary_user_text_is_not_an_inject() -> None:
    assert not is_harness_inject("List the files in the current project folder.")
    assert not is_harness_inject("")
    assert not is_harness_inject("[TODO] refactor the parser")


def test_the_nudge_itself_still_reads_as_trivia() -> None:
    """Pinning the trap: the inject *does* trip the trivia detector.

    This is why the extraction fix matters - the classifier is not wrong about
    the text it was handed, it was handed the wrong text.
    """
    assert is_pure_trivia_message(PROJECT_TREE_NUDGE)


def test_last_real_user_message_skips_injects() -> None:
    messages = [
        {"role": "system", "content": "You are Remedy."},
        {"role": "user", "content": "List the files in the current project folder."},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": PROJECT_TREE_NUDGE},
    ]
    assert last_real_user_message(messages) == (
        "List the files in the current project folder."
    )


def test_last_real_user_message_falls_back_to_inject_when_alone() -> None:
    """Over-arm rather than strip when every user turn is scaffolding."""
    messages = [{"role": "user", "content": PROJECT_TREE_NUDGE}]
    assert last_real_user_message(messages) == PROJECT_TREE_NUDGE


def test_local_body_keeps_tools_when_nudge_is_the_last_user_turn() -> None:
    """The regression itself: a real request must not lose its tools."""
    body = {
        "model": "gemma",
        "messages": [],
        "tools": _tools("list_dir", "file_read", "file_write"),
        "tool_choice": "auto",
        "max_tokens": 256,
    }
    history = [
        {"role": "user", "content": "List the files in the current project folder."},
        {"role": "user", "content": PROJECT_TREE_NUDGE},
    ]
    out = apply_local_body_optimize(
        body,
        user_message=last_real_user_message(history),
        step_index=0,
        history=history,
        **LOCAL,
    )
    assert len(out.get("tools") or []) == 3, "tools were stripped from a work request"
    assert int(out.get("max_tokens") or 0) > 256, "completion budget stayed at trivia cap"


def test_genuine_trivia_still_strips_tools() -> None:
    """The guard must not become a rubber stamp."""
    body = {
        "model": "gemma",
        "messages": [],
        "tools": _tools("list_dir", "file_write"),
        "tool_choice": "auto",
        "max_tokens": 2048,
    }
    history = [{"role": "user", "content": "2 + 2"}]
    out = apply_local_body_optimize(
        body,
        user_message=last_real_user_message(history),
        step_index=0,
        history=history,
        **LOCAL,
    )
    assert not out.get("tools")


def test_unbracketed_nudges_are_also_injects() -> None:
    """Not every inject carries a [Bracketed tag].

    RECOVERY_NUDGE is plain prose - "One or more tools failed. Do not give a
    final answer yet..." - so shape alone cannot distinguish it from owner
    input. It was being taken as the user's request, which made
    ``request_wants_execution`` false and let a green verify strip every tool
    mid-task. Matching the real constants keeps this exact.
    """
    from remedy.core import react_policy as rp

    for attr in (
        "RECOVERY_NUDGE",
        "EMPTY_WRITE_NUDGE",
        "SPEED_BATCH_NUDGE",
        "UNFINISHED_WORK_NUDGE",
    ):
        val = getattr(rp, attr, "")
        if isinstance(val, str) and val.strip():
            assert is_harness_inject(val), attr


def test_real_request_survives_a_recovery_nudge() -> None:
    from remedy.core.react_policy import RECOVERY_NUDGE

    ask = "Create fib.py that prints ten Fibonacci numbers, then run it."
    messages = [
        {"role": "system", "content": "You are Remedy."},
        {"role": "user", "content": ask},
        {"role": "user", "content": "[Build engine · GREEN · stop building]\nverify passed"},
        {"role": "user", "content": RECOVERY_NUDGE},
    ]
    assert last_real_user_message(messages) == ask
