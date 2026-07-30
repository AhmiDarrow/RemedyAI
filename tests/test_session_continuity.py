"""Session isolation — no stale brief/partner/work roots across tabs."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.session_continuity import (
    bind_session_continuity,
    session_isolation_system_line,
)
from remedy.memory.harness.brief import SessionBrief
from remedy.memory.partner_state.state import PartnerState, ensure_partner_state


def test_partner_state_rejects_foreign_session(tmp_path):
    rt = SimpleNamespace(
        _session_id="sess-a",
        config=SimpleNamespace(home_dir=str(tmp_path), project_path=""),
        _partner_state=None,
    )
    a = ensure_partner_state(rt)
    a.add_node(kind="artifact", text="secret", path=r"C:\Other\SecretFolder\x.rs")
    assert a.session_id == "sess-a"

    rt._session_id = "sess-b"
    b = ensure_partner_state(rt)
    assert b is not a
    assert b.session_id == "sess-b"
    assert not any(
        "SecretFolder" in (n.path or n.text)
        for n in b.nodes.values()
    )


def test_bind_swaps_brief_and_work_roots():
    rt = SimpleNamespace(
        _session_id="s1",
        _session_brief=SessionBrief(session_id="s1", intent="SecretFolder vault"),
        _work_roots=[r"C:\Users\Administrator\SecretFolder"],
        _partner_state=None,
        config=SimpleNamespace(home_dir=None, project_path=r"C:\Users\Administrator\SecretFolder"),
    )
    # fake effective path for isolation line later
    rt.effective_project_path = lambda: r"C:\Users\Administrator\SecretFolder"

    meta = bind_session_continuity(rt, "s2")
    assert meta["switched"] is True
    assert rt._session_id == "s2"
    assert rt._session_brief is not None
    assert rt._session_brief.session_id == "s2"
    assert rt._session_brief.intent != "SecretFolder vault"
    assert rt._work_roots == []

    # Switch back — restore s1 brief from cache
    meta2 = bind_session_continuity(rt, "s1")
    assert meta2["switched"] is True
    assert rt._session_brief.session_id == "s1"
    assert "SecretFolder" in (rt._session_brief.intent or "")
    assert any("SecretFolder" in r for r in (rt._work_roots or []))


def test_ensure_brief_rejects_foreign(tmp_path):
    from remedy.memory.harness.send_policy import _ensure_session_brief

    rt = SimpleNamespace(
        _session_id="alpha",
        _session_brief=SessionBrief(session_id="beta", intent="other tab"),
    )
    brief = _ensure_session_brief(rt, "alpha")
    assert brief.session_id == "alpha"
    assert brief.intent == ""


def test_isolation_system_line():
    rt = SimpleNamespace(
        _session_id="abc",
        effective_project_path=lambda: r"C:\Users\Administrator\RemedyAI",
    )
    line = session_isolation_system_line(rt)
    assert "Session isolation" in line
    assert "RemedyAI" in line
    assert "abc" in line
