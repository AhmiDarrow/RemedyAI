"""Tests for Memory Harness (L0 prune + L2 Session Brief + send policy)."""

from __future__ import annotations

from pathlib import Path

from remedy.memory.harness.brief import (
    DecisionRecord,
    SessionBrief,
    brief_to_context_block,
)
from remedy.memory.harness.offload import maybe_offload_messages, offload_tool_body
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


def test_brief_history_thread_and_decision_why():
    b = SessionBrief(session_id="y")
    b.add_decision_record(
        "Use SQLite",
        why="portable for desktop",
        rejected="Postgres (ops heavy)",
    )
    b.append_history_thread(
        "Explored schema and chose SQLite",
        decisions_why=["Use SQLite — Why: portable"],
        blockers=[],
    )
    b.append_history_thread("Implemented store layer")
    assert len(b.history_thread) == 2
    assert b.history_thread[0].n == 1
    block = brief_to_context_block(b)
    assert "Historical context" in block
    assert "do not re-litigate" in block.lower() or "SQLite" in block
    assert "portable" in block


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


def test_collapse_keeps_outcome_not_just_first_line():
    def fat(i: int) -> str:
        return (
            f"Reading C:/proj/src/app{i}.py\n"
            + ("line\n" * 80)
            + "ERROR: missing import\n"
            + "Failed at end\n"
        )

    msgs = [
        {"role": "user", "content": "go"},
        *[
            {
                "role": "tool",
                "tool_call_id": str(i),
                "name": "file_read",
                "content": fat(i),
            }
            for i in range(8)
        ],
    ]
    out = prune_messages_for_send(
        msgs,
        dedupe_tools=False,
        collapse_completed_tools=True,
        keep_recent_tool_pairs=2,
    )
    collapsed = [
        m["content"]
        for m in out
        if m.get("role") == "tool" and "collapsed" in m.get("content", "")
    ]
    assert collapsed
    assert any("ERR" in c or "error" in c.lower() for c in collapsed)
    assert any("app" in c and ".py" in c for c in collapsed)


def test_offload_fat_tool_body(tmp_path: Path):
    fat = "payload\n" * 5000
    handle, path = offload_tool_body(
        fat, session_id="s1", tool_name="bash_exec", home=tmp_path, min_chars=1000
    )
    assert path is not None
    assert "offloaded" in handle
    assert Path(path).is_file()
    msgs = [
        {"role": "tool", "tool_call_id": str(i), "name": "bash_exec", "content": fat}
        for i in range(6)
    ]
    out = maybe_offload_messages(
        msgs, session_id="s1", home=tmp_path, min_chars=1000, keep_recent_tools=2
    )
    assert any("offloaded" in (m.get("content") or "") for m in out[:3])
    # recent kept full
    assert fat in (out[-1].get("content") or "")
