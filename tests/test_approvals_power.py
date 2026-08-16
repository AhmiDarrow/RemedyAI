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


# ---------------------------------------------------------------------------
# Owner checkpoints — payment/purchase computer actions are non-waivable
# (docs/LIFE_TASK_PARTNER.md §2.2/§3; AGENTS.md north star Q3)
# ---------------------------------------------------------------------------


def _cfg(monkeypatch, **extra):
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", **extra},
    )


def test_payment_click_asks_in_every_mode(monkeypatch):
    """'Place order' must checkpoint in ask, auto, AND full — no waiver."""
    from remedy.core.approvals import SENSITIVE_PREFIX

    _cfg(monkeypatch)
    for mode in ("ask", "auto", "full"):
        q = ApprovalQueue()
        q.set_mode(mode)
        reason = q.needs_ask(
            "act url='https://www.amazon.com/checkout' click='Place order' "
            "type_chars=0 key='' goal='order paper towels' target=auto",
            tool_name="computer_act",
        )
        assert reason is not None, mode
        assert reason.startswith(SENSITIVE_PREFIX), (mode, reason)


def test_payment_checkpoint_ignores_turn_skip_ask(monkeypatch):
    """turn_skip_ask (local-power turns) must NOT bypass owner checkpoints."""
    _cfg(monkeypatch)
    monkeypatch.setattr("remedy.core.turn_context.turn_skip_ask", lambda: True)
    q = ApprovalQueue()
    q.set_mode("auto")
    reason = q.needs_ask(
        "click text='Pay now' ref='' x=0 y=0 button=left clicks=1 target=auto",
        tool_name="computer_click",
    )
    assert reason is not None
    assert "checkpoint" in reason.lower()


def test_coding_tools_keep_full_flow(monkeypatch):
    """Frontier coding agency untouched: bash/file tools never trip the
    payment classifier, and auto/full stay promptless for them."""
    _cfg(monkeypatch)
    for mode in ("auto", "full"):
        q = ApprovalQueue()
        q.set_mode(mode)
        # Even payment-looking text in shell/file work must not checkpoint —
        # the sensitive tier is computer_* only.
        assert (
            q.needs_ask("python pay_now_report.py --confirm-payment", tool_name="bash_exec")
            is None
        ), mode
        assert q.needs_ask("write src/checkout/place_order.py", tool_name="file_write") is None
        assert q.needs_ask("edit src/billing/cvv_mask.py", tool_name="file_edit") is None


def test_ordinary_computer_clicks_not_checkpointed_in_auto(monkeypatch):
    """Non-payment clicks keep the auto contract (no prompt fatigue)."""
    _cfg(monkeypatch)
    q = ApprovalQueue()
    q.set_mode("auto")
    assert (
        q.needs_ask(
            "act url='https://www.amazon.com' click='Add to Cart' target=auto",
            tool_name="computer_act",
        )
        is None
    )
    assert (
        q.needs_ask("click text='Next' target=auto", tool_name="computer_click")
        is None
    )


def test_sensitive_approval_is_one_shot_not_persisted(monkeypatch):
    """A payment go-ahead is single-use — never a persisted session/always
    fingerprint (prevents cross-site / later replay; reviewer P0)."""
    _cfg(monkeypatch)
    q = ApprovalQueue()
    q.set_mode("ask")
    cmd = "click text='Place order' target=auto"
    reason = q.needs_ask(cmd, tool_name="computer_click")
    assert reason
    item = q.create(
        tool_name="computer_click", command=cmd, reason=reason, session_id="s1"
    )
    assert item.sensitive is True
    q.resolve(item.id, approve=True, scope="always")  # even "always"
    # No persisted approval anywhere
    assert item.fingerprint not in q._approved_fps
    assert item.fingerprint not in q._session_fps.get("s1", set())
    # is_approved must NOT cover it
    assert q.is_approved("computer_click", cmd, session_id="s1") is False
    # One-shot grant is consumed exactly once
    assert q.take_one_shot("computer_click", cmd, session_id="s1") is True
    assert q.take_one_shot("computer_click", cmd, session_id="s1") is False


def test_sensitive_grant_does_not_replay_cross_site(monkeypatch):
    """One approval cannot silently authorize the same action elsewhere.

    computer_click summaries carry no URL, so before the fix an approved
    'Place order' replayed on any site. One-shot consumption blocks the
    second use. Uses the shared APPROVALS singleton (the gate imports it),
    so restore its state afterwards to avoid polluting other tests."""
    from remedy.core.agent_computer_tools import _computer_approval_gate

    _cfg(monkeypatch)
    from remedy.core import approvals as ap

    q = ap.APPROVALS
    prev_mode = q.mode
    prev_one_shot = {k: set(v) for k, v in q._one_shot.items()}
    try:
        q.set_mode("full")
        q._one_shot.clear()

        summary = "click text='Place order' ref='' x=0 y=0 button=left clicks=1 target=auto"

        class RT:  # minimal runtime; turn_session_id falls back gracefully
            pass

        blocked1 = _computer_approval_gate(RT(), "computer_click", summary)
        assert blocked1 and "APPROVAL_REQUIRED" in blocked1
        # Owner approves the pending item
        import re as _re

        aid = _re.search(r"id=(\w+)", blocked1).group(1)
        q.resolve(aid, approve=True, scope="session")
        # First retry proceeds (grant consumed)
        assert _computer_approval_gate(RT(), "computer_click", summary) is None
        # A later identical action (e.g. injected click on another site) re-prompts
        assert _computer_approval_gate(RT(), "computer_click", summary) is not None
    finally:
        q.set_mode(prev_mode)
        q._one_shot.clear()
        q._one_shot.update(prev_one_shot)


def test_mode_flip_leaves_sensitive_pending(monkeypatch):
    """Switching to auto/full auto-approves ordinary prompts but never a
    payment checkpoint."""
    _cfg(monkeypatch)
    q = ApprovalQueue()
    q.set_mode("ask")
    ordinary = q.create(
        tool_name="computer_click",
        command="click text='Next'",
        reason="Computer control requires approval (computer_click)",
        session_id="s1",
    )
    payment = q.create(
        tool_name="computer_click",
        command="click text='Place order'",
        reason=q.needs_ask(
            "click text='Place order'", tool_name="computer_click"
        )
        or "",
        session_id="s1",
    )
    q.set_mode("full")
    assert q.get(ordinary.id).status == "approved"
    assert q.get(payment.id).status == "pending"


def test_plain_summary_is_owner_legible(monkeypatch):
    """Approval cards lead with plain language, not tool jargon."""
    _cfg(monkeypatch)
    q = ApprovalQueue()
    item = q.create(
        tool_name="computer_act",
        command=(
            "act url='https://www.amazon.com/checkout' click='Place order' "
            "type_chars=0 key='' goal='' target=auto"
        ),
        reason=q.needs_ask(
            "act url='https://www.amazon.com/checkout' click='Place order'",
            tool_name="computer_act",
        )
        or "",
        session_id="s1",
    )
    pub = q.to_public(item)
    assert pub["sensitive"] is True
    assert pub["summary"].startswith("Remedy wants")
    assert "Place order" in pub["summary"]
    assert "payment" in pub["summary"].lower()
    # Non-sensitive click card
    item2 = q.create(
        tool_name="computer_click",
        command="click text='Sign in' ref='' x=0 y=0 button=left clicks=1 target=auto",
        reason="Computer control requires approval (computer_click)",
        session_id="s1",
    )
    pub2 = q.to_public(item2)
    assert pub2["sensitive"] is False
    assert "Sign in" in pub2["summary"]
    assert "computer_click" not in pub2["summary"]
