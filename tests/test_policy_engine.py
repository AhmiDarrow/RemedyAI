"""v0.32 M1.3 — PolicyEngine matches today's needs_ask / dangerous-command."""

from __future__ import annotations

import pytest

from remedy.core.approvals import APPROVALS
from remedy.core.context import TurnFactory
from remedy.core.turn_context import begin_turn, end_turn
from remedy.policy.decisions import ToolRequest
from remedy.policy.engine import PolicyEngine

_CFG: dict = {"access_scope": "project"}


@pytest.fixture(autouse=True)
def _isolate_approval_config(monkeypatch):
    _CFG.clear()
    _CFG.update({"access_scope": "project"})
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: dict(_CFG),
    )


def _eval(name: str, command: str = "", *, mode: str = "ask"):
    prev = APPROVALS.mode
    APPROVALS.set_mode(mode)
    tokens = begin_turn("policy-sess", project_raw=None, active_path=".")
    try:
        ctx = TurnFactory.create()
        return PolicyEngine().evaluate(
            ctx, name, ToolRequest(name=name, command=command)
        )
    finally:
        end_turn("policy-sess", *tokens)
        APPROVALS.set_mode(prev)


def test_ask_mode_shell_requires_approval_but_is_allowed():
    d = _eval("bash_exec", "echo hi", mode="ask")
    assert d.allowed is True
    assert d.requires_approval is True
    assert "bash_exec" in d.reason or "Shell" in d.reason


def test_auto_mode_skips_high_impact_prompt():
    d = _eval("bash_exec", "echo hi", mode="auto")
    assert d.allowed is True
    assert d.requires_approval is False


def test_full_mode_skips_high_impact_prompt():
    d = _eval("file_write", "notes.txt", mode="full")
    assert d.allowed is True
    assert d.requires_approval is False


def test_mail_send_always_asks():
    d = _eval("mail_send", "to=a@b.c", mode="full")
    assert d.allowed is True
    assert d.requires_approval is True
    assert "mail" in d.reason.lower() or "Owner checkpoint" in d.reason


def test_dangerous_command_is_denied():
    d = _eval("bash_exec", "rm -rf /", mode="full")
    assert d.allowed is False
    assert d.requires_approval is False


def test_autonomous_trust_skips_high_impact_in_ask_mode():
    _CFG["trust_profile"] = "autonomous"
    d = _eval("bash_exec", "echo hi", mode="ask")
    assert d.allowed is True
    assert d.requires_approval is False
    assert d.reason == "allowed"


def test_autonomous_trust_still_asks_mail_checkpoint():
    _CFG["trust_profile"] = "autonomous"
    d = _eval("mail_send", "to=a@b.c", mode="ask")
    assert d.allowed is True
    assert d.requires_approval is True
    assert "Owner checkpoint" in d.reason or "mail" in d.reason.lower()


def test_conservative_trust_does_not_skip_in_ask_mode():
    _CFG["trust_profile"] = "conservative"
    d = _eval("bash_exec", "echo hi", mode="ask")
    assert d.allowed is True
    assert d.requires_approval is True


def test_balanced_trust_default_does_not_skip_in_ask_mode():
    # No trust_profile key → BALANCED
    d = _eval("file_write", "notes.txt", mode="ask")
    assert d.allowed is True
    assert d.requires_approval is True
