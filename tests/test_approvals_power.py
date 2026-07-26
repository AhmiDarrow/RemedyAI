"""Approval queue: safe default Ask, Auto preserves full owner power."""

from __future__ import annotations

from remedy.core.approvals import ApprovalQueue


def test_auto_mode_skips_high_impact_on_trusted_scope(monkeypatch):
    """Work-until-done: auto never prompts on normal/project scope."""
    q = ApprovalQueue()
    q.set_mode("auto")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    assert q.needs_ask("rm -rf /tmp/foo", tool_name="bash_exec") is None
    assert q.needs_ask("write src/a.py", tool_name="file_write") is None
    assert q.needs_ask("edit src/a.py", tool_name="file_edit") is None
    assert q.needs_ask("run skill", tool_name="skill_run") is None


def test_ask_mode_requires_bash_and_soft_risk(monkeypatch):
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    reason = q.needs_ask("echo hi", tool_name="bash_exec")
    assert reason is not None
    assert "bash_exec" in reason.lower() or "shell" in reason.lower()


def test_untrusted_scope_asks_even_in_auto(monkeypatch):
    q = ApprovalQueue()
    q.set_mode("auto")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "untrusted"},
    )
    reason = q.needs_ask("echo hi", tool_name="bash_exec")
    assert reason is not None
    assert "untrusted" in reason.lower()


def test_public_payload_includes_mode_hint():
    q = ApprovalQueue()
    item = q.create(
        tool_name="bash_exec",
        command="echo x",
        reason="Shell execution requires approval (bash_exec)",
        session_id="s1",
    )
    pub = q.to_public(item)
    assert pub["id"] == item.id
    assert "approval_mode_hint" in pub
    assert "Auto" in pub["approval_mode_hint"] or "auto" in pub["approval_mode_hint"].lower()


def test_set_auto_clears_pending():
    """Thumbs-up must clear the banner and unlock fingerprints."""
    q = ApprovalQueue()
    q.set_mode("ask")
    item = q.create(
        tool_name="bash_exec",
        command="python process_assets.py",
        reason="Shell execution requires approval (bash_exec)",
        session_id="s1",
    )
    assert item.status == "pending"
    assert len(q.list_pending()) == 1
    q.set_mode("auto")
    assert item.status == "approved"
    assert q.list_pending() == []
    assert q.is_approved("bash_exec", "python process_assets.py", session_id="s1")


def test_sync_from_config_makes_auto_stick(monkeypatch):
    """config.toml auto must win over process-local ask after restart."""
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "project"},
    )
    assert q.needs_ask("echo hi", tool_name="bash_exec") is None
    assert q.mode == "auto"


def test_config_to_agent_config_carries_approval_mode():
    from remedy.interfaces.config import config_to_agent_config

    cfg = config_to_agent_config(
        {
            "approval_mode": "auto",
            "access_scope": "project",
            "llm_provider": "xai",
            "llm_model": "grok-4.5",
        }
    )
    assert cfg.approval_mode == "auto"
    assert cfg.access_scope == "project"
