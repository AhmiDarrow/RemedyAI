"""Partner State Machine — subgoals, tool txns, graph, prospective, continuity."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.memory.harness.brief import SessionBrief
from remedy.memory.harness.pruner import prune_messages_for_send
from remedy.memory.partner_state.state import PartnerState, ensure_partner_state


def test_scrub_preview_redacts_secrets():
    from remedy.memory.partner_state.state import _scrub_preview

    raw = "ok api_key=sk-abcdefghijklmnopqrstuvwxyz0123 done"
    out = _scrub_preview(raw, limit=240)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in out
    assert "[redacted]" in out


def test_scrub_preview_fail_closed_on_redact_error(monkeypatch):
    import remedy.memory.partner_state.state as ps

    def boom(_text: str) -> str:
        raise RuntimeError("redact broken")

    monkeypatch.setattr(
        "remedy.core.metabolism.redact.redact_text",
        boom,
        raising=False,
    )
    # Import path inside _scrub_preview — patch module after import
    import remedy.core.metabolism.redact as redact_mod

    monkeypatch.setattr(redact_mod, "redact_text", boom)
    out = ps._scrub_preview("api_key=sk-abcdefghijklmnopqrstuvwxyz0123", limit=80)
    assert out == "[redacted]"
    assert "sk-" not in out


def test_subgoal_protects_tool_results():
    st = PartnerState(session_id="sg-test")
    sg = st.open_subgoal("Implement write jail")
    st.record_tool(
        name="file_read",
        args={"path": "src/a.py"},
        result="BODY_" + ("x" * 500),
        success=True,
        tool_call_id="call_protect_me",
    )
    assert "call_protect_me" in st.protected_tool_call_ids()
    assert st.active_subgoal_id == sg.id

    fat = "OLD_" + ("y" * 800)
    msgs = [
        {"role": "user", "content": "go"},
        *[{"role": "tool", "tool_call_id": f"old{i}", "name": "bash_exec", "content": fat} for i in range(10)],
        {
            "role": "tool",
            "tool_call_id": "call_protect_me",
            "name": "file_read",
            "content": "BODY_" + ("x" * 500),
        },
    ]
    out = prune_messages_for_send(
        msgs,
        collapse_completed_tools=True,
        keep_recent_tool_pairs=2,
        protect_tool_call_ids=st.protected_tool_call_ids(),
    )
    protected = next(m for m in out if m.get("tool_call_id") == "call_protect_me")
    assert "BODY_" in protected["content"]
    assert "collapsed" not in protected["content"]


def test_write_set_and_verify(tmp_path: Path):
    st = PartnerState(session_id="ws", home=tmp_path)
    st.record_tool(
        name="file_edit",
        args={"path": str(tmp_path / "app.py")},
        result="ok edited",
        success=True,
        tool_call_id="w1",
    )
    assert st.unverified_writes()
    assert st.verify_write(str(tmp_path / "app.py"), how="tests")
    assert not st.unverified_writes()
    st.save()
    st2 = PartnerState(session_id="ws", home=tmp_path)
    assert st2.load()
    assert st2.tool_txns


def test_tool_recall_offload(tmp_path: Path):
    st = PartnerState(session_id="rc", home=tmp_path)
    body = "FULL_RESULT\n" * 100
    off = tmp_path / "off.txt"
    off.write_text(body, encoding="utf-8")
    txn = st.record_tool(
        name="bash_exec",
        args={},
        result="preview",
        success=True,
        tool_call_id="t1",
        offload_path=str(off),
    )
    text = st.recall_txn_body(txn_id=txn.id)
    assert "FULL_RESULT" in text


def test_epistemic_projects_to_brief():
    st = PartnerState(session_id="ep")
    st.add_node(kind="decision", text="Use SQLite", why="portable", source="agent")
    st.add_node(kind="artifact", text="store.py", path="src/store.py", source="tool")
    st.add_node(kind="commitment", text="Never force-push main", source="user")
    brief = SessionBrief(session_id="ep")
    st.apply_graph_to_brief(brief)
    assert any("SQLite" in d for d in brief.decisions)
    assert any("store.py" in a for a in brief.artifacts)
    assert any("force-push" in c for c in brief.user_constraints)


def test_prospective_session_start():
    st = PartnerState(session_id="pr")
    st.add_prospective("Mention write-jail tests on PR", trigger="session_start")
    fired = st.fire_prospectives("session_start")
    assert len(fired) == 1
    assert "write-jail" in fired[0].text


def test_dual_stream_blocks():
    st = PartnerState(session_id="ds")
    st.open_subgoal("Wire partner state")
    st.add_node(kind="commitment", text="Prefer local-first", source="user")
    st.record_tool(
        name="file_write",
        args={"path": "src/x.py"},
        result="wrote",
        success=True,
        tool_call_id="c1",
    )
    partner, project = st.dual_stream_blocks()
    assert "Partner stream" in partner
    assert "Prefer local-first" in partner
    assert "Project stream" in project
    assert "Active subgoal" in project
    assert "Unverified writes" in project


def test_continuity_tick():
    st = PartnerState(session_id="cc")
    brief = SessionBrief()
    st.add_node(kind="decision", text="Ship Phase A first")
    out = st.continuity_tick(brief=brief)
    assert out["passes"] == 1
    assert brief.decisions


def test_close_subgoal_promotes_decision():
    st = PartnerState(session_id="cl")
    st.open_subgoal("Refactor pruner")
    st.close_subgoal(summary="Keep protect_tool_call_ids")
    nodes = st.graph_active("decision")
    assert any("Refactor pruner" in n.text for n in nodes)


def test_ensure_partner_state_on_runtime(tmp_path: Path):
    rt = SimpleNamespace(
        _session_id="rt1",
        config=SimpleNamespace(home_dir=str(tmp_path), project_path=""),
        _partner_state=None,
    )
    a = ensure_partner_state(rt)
    b = ensure_partner_state(rt)
    assert a is b
    assert rt._partner_state is a


def test_graph_quality_coverage():
    st = PartnerState(session_id="gq")
    st.add_node(kind="artifact", text="app", path="src/app.ts")
    st.add_node(kind="decision", text="use vite for bundling")
    cov = st.graph_quality_coverage(
        paths=["src/app.ts", "missing.py"],
        decisions=["use vite for bundling", "totally unrelated phrase xyz"],
    )
    assert cov["paths_kept"] >= 1
    assert cov["score"] > 0.3
