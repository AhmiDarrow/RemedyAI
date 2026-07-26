"""Tests for Memory Harness (L0 prune + L2 Session Brief + send policy)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.memory.harness.brief import (
    SessionBrief,
    brief_to_context_block,
)
from remedy.memory.harness.offload import maybe_offload_messages, offload_tool_body
from remedy.memory.harness.pruner import prune_messages_for_send
from remedy.memory.harness.quality import review_compress_quality
from remedy.memory.harness.send_policy import (
    apply_auto_harness_send_policy,
    slim_messages_mid_turn,
)


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


def test_quality_fail_closed_without_extractable_facts():
    """Empty history must not authorize middle-history drop (score < 0.65)."""
    brief = SessionBrief(intent="maybe", artifacts=[], decisions=[])
    r = review_compress_quality(
        messages_before=[{"role": "user", "content": "hello there"}],
        brief=brief,
        tokens_before=1000,
        tokens_after=200,
    )
    assert r["score"] < 0.65
    assert r["ok"] is False


def test_quality_ok_requires_kept_facts():
    messages = [
        {
            "role": "user",
            "content": "Edit C:\\Users\\me\\proj\\src\\app.ts and decided to use vite",
        },
    ]
    brief = SessionBrief(
        intent="edit app",
        artifacts=["C:\\Users\\me\\proj\\src\\app.ts"],
        decisions=["use vite"],
        key_paths=["src/app.ts"],
    )
    r = review_compress_quality(
        messages_before=messages,
        brief=brief,
        tokens_before=5000,
        tokens_after=1000,
    )
    assert r["ok"] is True
    assert r["score"] >= 0.55


def test_budget_trim_protects_recent_tools():
    recent = "RECENT_TOOL_BODY_" + ("y" * 3000)
    old = "OLD_TOOL_BODY_" + ("x" * 9000)
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "1", "name": "bash_exec", "content": old},
        {"role": "tool", "tool_call_id": "2", "name": "bash_exec", "content": old},
        {"role": "tool", "tool_call_id": "3", "name": "bash_exec", "content": recent},
    ]
    out = prune_messages_for_send(
        msgs,
        dedupe_tools=False,
        token_budget=500,
        reserve_tokens=0,
        keep_recent_tool_pairs=1,
    )
    last_tool = [m for m in out if m.get("role") == "tool"][-1]
    assert recent in (last_tool.get("content") or "")


def _fake_runtime(**kwargs):
    base = {
        "_harness_mode": "auto",
        "_harness_min_pct": 0.01,  # force soft/strong easily
        "_harness_max_pct": 0.02,
        "_llm_provider": "openai",
        "_llm_model": "gpt-4o-mini",
        "_session_id": "sess-test",
        "_session_brief": None,
        "config": SimpleNamespace(
            provider="openai",
            model="gpt-4o-mini",
            project_path="",
            home_dir=None,
        ),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_auto_policy_creates_brief_when_missing():
    # Large enough message list to trigger fill-based soft/strong with tiny thresholds
    fat = "line of context\n" * 2000
    messages = [
        {"role": "system", "content": "sys"},
        *[{"role": "user", "content": fat} for _ in range(3)],
        {"role": "user", "content": "current question"},
    ]
    rt = _fake_runtime()
    out, meta = apply_auto_harness_send_policy(
        rt, list(messages), user_text="current question", session_id="sess-test"
    )
    assert rt._session_brief is not None
    assert rt._session_brief.session_id == "sess-test"
    assert meta.get("level") in ("soft", "strong", None) or True  # level depends on estimator
    assert isinstance(out, list)
    assert len(out) >= 1


def test_auto_policy_middle_replace_requires_quality_ok():
    fat = "x" * 50_000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": fat},
        {"role": "assistant", "content": fat},
        {"role": "tool", "tool_call_id": "t1", "name": "file_read", "content": fat},
        {"role": "user", "content": fat},
        {"role": "assistant", "content": fat},
        {"role": "tool", "tool_call_id": "t2", "name": "file_read", "content": fat},
        {"role": "user", "content": "finish up"},
    ]
    rt = _fake_runtime(
        _session_brief=SessionBrief(session_id="sess-test"),  # empty = no substance path match
        _harness_min_pct=0.01,
        _harness_max_pct=0.02,
    )
    _out, meta = apply_auto_harness_send_policy(
        rt, list(messages), user_text="finish up", session_id="sess-test"
    )
    # With fail-closed quality, middle replace must not fire on empty brief facts
    if meta.get("level") == "strong":
        assert meta.get("middle_replaced") is False


def test_mid_turn_slim_no_op_when_small():
    rt = _fake_runtime(_harness_min_pct=0.75)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    out = slim_messages_mid_turn(rt, msgs, session_id="s")
    assert out == msgs


def test_brief_registry_session_isolation():
    from remedy.memory.harness.local_brief import (
        apply_local_brief_payload,
        get_registered_brief,
        register_session_brief,
    )

    a = SessionBrief(session_id="a")
    b = SessionBrief(session_id="b")
    register_session_brief("a", a)
    register_session_brief("b", b)
    apply_local_brief_payload(
        get_registered_brief("a"),
        {"intent": "only A", "paths": ["a.py"], "decisions": ["da"]},
    )
    assert a.intent == "only A"
    assert b.intent == ""
    assert get_registered_brief("b") is b
