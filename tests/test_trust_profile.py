from __future__ import annotations

from remedy.core.approvals import APPROVALS
from remedy.core.context import TurnFactory
from remedy.core.trust_profile import (
    TrustProfile,
    checkpoint_still_required,
    profile_skips_high_impact_ask,
)
from remedy.core.turn_context import begin_turn, end_turn
from remedy.policy.decisions import ToolRequest
from remedy.policy.engine import PolicyEngine, _resolve_trust_profile


def test_autonomous_does_not_waive_mail_or_pay():
    assert profile_skips_high_impact_ask(TrustProfile.AUTONOMOUS) is True
    assert checkpoint_still_required("mail_send", "hello") is not None
    assert checkpoint_still_required("mail_reply", "re: invoice") is not None
    pay = checkpoint_still_required("computer_click", "Place your order")
    assert pay is not None
    assert "Owner checkpoint" in pay


def test_conservative_and_balanced_still_ask():
    assert profile_skips_high_impact_ask(TrustProfile.CONSERVATIVE) is False
    assert profile_skips_high_impact_ask(TrustProfile.BALANCED) is False
    assert checkpoint_still_required("bash_exec", "echo hi") is None


def test_resolve_trust_profile_defaults_balanced(monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    assert _resolve_trust_profile() == TrustProfile.BALANCED


def test_resolve_trust_profile_reads_config(monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"trust_profile": "autonomous"},
    )
    assert _resolve_trust_profile() == TrustProfile.AUTONOMOUS


def test_resolve_trust_profile_invalid_falls_back(monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"trust_profile": "not-a-real-profile"},
    )
    assert _resolve_trust_profile() == TrustProfile.BALANCED


def test_policy_engine_autonomous_skips_shell_not_mail(monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "trust_profile": "autonomous"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("ask")
    tokens = begin_turn("trust-pol", project_raw=None, active_path=".")
    try:
        ctx = TurnFactory.create()
        eng = PolicyEngine()
        shell = eng.evaluate(
            ctx, "bash_exec", ToolRequest(name="bash_exec", command="echo hi")
        )
        assert shell.requires_approval is False
        mail = eng.evaluate(
            ctx, "mail_send", ToolRequest(name="mail_send", command="hi")
        )
        assert mail.requires_approval is True
    finally:
        end_turn("trust-pol", *tokens)
        APPROVALS.set_mode(prev)
