"""Last-phase partner loop: approvals, knowledge pack, goals helpers."""

from __future__ import annotations

from pathlib import Path

from remedy.core.approvals import APPROVALS, ApprovalQueue


def test_approval_needs_ask_destructive(monkeypatch):
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "approval_mode": "ask"},
    )
    assert q.needs_ask("rm -rf /tmp/foo") is not None
    assert q.needs_ask("git reset --hard HEAD") is not None
    assert q.needs_ask("ls -la") is None


def test_approval_session_scope():
    q = ApprovalQueue()
    item = q.create(
        tool_name="bash_exec",
        command="rm -rf ./build",
        reason="test",
        session_id="s1",
    )
    assert not q.is_approved("bash_exec", "rm -rf ./build", session_id="s1")
    q.resolve(item.id, approve=True, scope="session")
    assert q.is_approved("bash_exec", "rm -rf ./build", session_id="s1")
    assert not q.is_approved("bash_exec", "rm -rf ./build", session_id="other")


def test_singleton_approvals_create_list():
    # Use global carefully — create unique command
    cmd = f"rm -rf ./unique-test-{Path.cwd().name}"
    item = APPROVALS.create(
        tool_name="bash_exec",
        command=cmd,
        reason="unit",
        session_id="unit",
    )
    pending = APPROVALS.list_pending(session_id="unit")
    assert any(p.id == item.id for p in pending)
    APPROVALS.resolve(item.id, approve=False)
