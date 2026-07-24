"""Tests for Memory Harness (L0 prune + L2 Session Brief)."""

from __future__ import annotations

from remedy.memory.harness.brief import SessionBrief, brief_to_context_block
from remedy.memory.harness.pruner import prune_messages_for_send


def test_session_brief_context_block():
    brief = SessionBrief(
        session_id="s1",
        intent="Ship Memory Harness",
        decisions=["Use Session Brief for L2"],
        artifacts=["src/remedy/memory/harness/brief.py"],
        next_steps=["Add compress tool"],
    )
    block = brief_to_context_block(brief)
    assert "Session Brief" in block
    assert "Ship Memory Harness" in block
    assert "brief.py" in block


def test_session_brief_empty():
    assert brief_to_context_block(SessionBrief()) == ""
    assert brief_to_context_block(None) == ""


def test_brief_add_artifact_and_merge():
    b = SessionBrief(session_id="x")
    b.add_artifact("a.py")
    b.add_artifact("a.py")  # dedupe
    assert b.artifacts == ["a.py"]
    b.merge_summary(
        intent="Fix bug",
        decisions=["Root cause is null"],
        next_steps=["Write test"],
    )
    assert b.intent == "Fix bug"
    assert b.compress_count == 1
    assert "Root cause is null" in b.decisions


def test_prune_truncates_huge_tool_output():
    huge = "x" * 20_000
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "1", "content": huge},
    ]
    out = prune_messages_for_send(msgs, max_tool_chars=1000)
    assert len(out[1]["content"]) < 2000
    assert "harness truncated" in out[1]["content"]


def test_prune_dedupes_identical_tool_results():
    body = "same tool payload"
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "a", "name": "file_read", "content": body},
        {"role": "assistant", "content": "ok"},
        {"role": "tool", "tool_call_id": "b", "name": "file_read", "content": body},
    ]
    out = prune_messages_for_send(msgs, dedupe_tools=True)
    tool_bodies = [m["content"] for m in out if m.get("role") == "tool"]
    assert any("duplicate tool result" in c for c in tool_bodies)
    assert any(c == body for c in tool_bodies)
