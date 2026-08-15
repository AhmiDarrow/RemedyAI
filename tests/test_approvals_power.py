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
    assert q.needs_ask("click text='OK'", tool_name="computer_click") is None
    assert q.needs_ask("type chars=8", tool_name="computer_type") is None
    assert q.needs_ask("act url=…", tool_name="computer_act") is None


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


def test_ask_mode_requires_computer_mutation(monkeypatch):
    """OS click/type/act/app must prompt in Ask mode (same bar as shell)."""
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    for tool in (
        "computer_click",
        "computer_type",
        "computer_key",
        "computer_drag",
        "computer_act",
        "computer_app",
    ):
        reason = q.needs_ask(f"summary for {tool}", tool_name=tool)
        assert reason is not None, tool
        assert "computer" in reason.lower(), reason


def test_ask_mode_requires_mail_send(monkeypatch):
    """Gmail send is high-impact — Ask mode must prompt (no silent send)."""
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    reason = q.needs_ask(
        "mail_send to=a@b.com subject=hi", tool_name="mail_send"
    )
    assert reason is not None
    assert "mail" in reason.lower() or "email" in reason.lower()
    q.set_mode("auto")
    assert q.needs_ask("mail_send to=a@b.com subject=hi", tool_name="mail_send") is None


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


def test_ship_gate_respects_auto(monkeypatch):
    """Live 2026-08-13: Auto on, git_push still created a banner via `or fallback`."""
    from remedy.core.agent_ship_tools import approval_required_for_ship
    from remedy.core.approvals import APPROVALS

    APPROVALS.set_mode("auto")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "full"},
    )
    assert (
        approval_required_for_ship(
            "git push -u origin HEAD", "sess", reason="git push (ship)"
        )
        is None
    )
    assert (
        approval_required_for_ship(
            "gh release create v1.0.8", "sess", reason="gh release create (ship)"
        )
        is None
    )


def test_ship_gate_still_asks_in_ask_mode(monkeypatch):
    from remedy.core.agent_ship_tools import approval_required_for_ship
    from remedy.core.approvals import APPROVALS

    APPROVALS.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "ask", "access_scope": "full"},
    )
    blob = approval_required_for_ship(
        "git push -u origin HEAD", "sess", reason="git push (ship)"
    )
    assert blob and blob.startswith("APPROVAL_REQUIRED")


def test_full_mode_skips_prompts_even_on_untrusted(monkeypatch):
    q = ApprovalQueue()
    q.set_mode("full")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "full", "access_scope": "untrusted"},
    )
    assert q.needs_ask("rm -rf /tmp/foo", tool_name="bash_exec") is None
    assert q.needs_ask("write src/a.py", tool_name="file_write") is None


def test_set_full_clears_pending():
    q = ApprovalQueue()
    q.set_mode("ask")
    item = q.create(
        tool_name="bash_exec",
        command="python process_assets.py",
        reason="Shell execution requires approval (bash_exec)",
        session_id="s1",
    )
    assert item.status == "pending"
    q.set_mode("full")
    assert item.status == "approved"
    assert q.list_pending() == []


def test_normalize_approval_mode_aliases():
    from remedy.core.approvals import normalize_approval_mode

    assert normalize_approval_mode("ask") == "ask"
    assert normalize_approval_mode("auto") == "auto"
    assert normalize_approval_mode("full") == "full"
    assert normalize_approval_mode("yolo") == "full"
    assert normalize_approval_mode("trust") == "auto"
    assert normalize_approval_mode("nope") == "ask"


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
    full = config_to_agent_config(
        {
            "approval_mode": "full",
            "access_scope": "project",
            "llm_provider": "xai",
            "llm_model": "grok-4.5",
        }
    )
    assert full.approval_mode == "full"
    omitted = config_to_agent_config(
        {
            "access_scope": "project",
            "llm_provider": "xai",
            "llm_model": "grok-4.5",
        }
    )
    assert omitted.approval_mode == "auto"
