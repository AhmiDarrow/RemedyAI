from __future__ import annotations

from remedy.core.context_budget import ContextBudget, clip_history, clip_tool_result


def test_clip_tool_result_keeps_head_and_tail():
    text = "A" * 1000 + "MID" + "B" * 1000
    out = clip_tool_result(text, ContextBudget(tool_result_chars=80))
    assert out.startswith("A")
    assert out.endswith("B")
    assert "[stored artifact]" in out
    assert "MID" not in out
    assert len(out) < len(text)
    assert len(out) <= 80


def test_clip_history_keeps_first_and_newest():
    msgs = ["goal", "old1", "old2", "new"]
    original = list(msgs)
    out = clip_history(msgs, ContextBudget(history_chars=8))
    assert out[0] == "goal"
    assert "new" in out
    assert "old1" not in out
    assert "old2" not in out
    assert msgs == original


def test_clip_history_keeps_oversized_goal():
    msgs = ["G" * 50, "new"]
    out = clip_history(msgs, ContextBudget(history_chars=10))
    assert out[0] == msgs[0]
    assert out == [msgs[0]]
