"""v0.38 gates — capability auth, credentials, isolation, evidence, explain.

Documents the Optimization/Stability close-out:

- capability / policy still authorize tools (committed PolicyEngine)
- generic ``child_environment()`` must not inherit ambient ``GH_TOKEN``
- web facts are TOOL_OBSERVED, never USER_DECLARED
- AUTONOMOUS does not waive mail_send / payment owner checkpoints
- ``explain_turn`` summarizes what / why / verified / remains
"""

from __future__ import annotations

from pathlib import Path

from remedy.core.context import TurnFactory
from remedy.core.trust_profile import (
    TrustProfile,
    checkpoint_still_required,
    profile_skips_high_impact_ask,
)
from remedy.core.turn_context import begin_turn, current_turn_id, end_turn
from remedy.credentials.broker import child_environment
from remedy.memory.provenance import SourceType, ingest_web_text
from remedy.policy.capabilities import Capability
from remedy.policy.decisions import ToolRequest
from remedy.policy.engine import PolicyEngine
from remedy.tools.catalog import descriptor_for


def test_v038_capability_and_policy(tmp_path: Path):
    d = descriptor_for("bash_exec")
    assert Capability.PROCESS_EXEC in d.capabilities
    tokens = begin_turn("s", project_raw=None, active_path=str(tmp_path))
    try:
        ctx = TurnFactory.create()
        assert ctx.turn_id == current_turn_id()
        decision = PolicyEngine().evaluate(
            ctx, d, ToolRequest(name="bash_exec", command="echo hi")
        )
        assert decision.allowed is True
    finally:
        end_turn("s", *tokens)


def test_v038_generic_shell_has_no_ambient_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    env = child_environment()
    assert "GH_TOKEN" not in env


def test_v038_web_facts_never_user_declared():
    fact = ingest_web_text("secret")
    assert fact.source_type == SourceType.TOOL_OBSERVED
    assert fact.source_type != SourceType.USER_DECLARED
    assert fact.user_confirmed is False


def test_v038_autonomous_does_not_waive_owner_checkpoints():
    assert profile_skips_high_impact_ask(TrustProfile.AUTONOMOUS) is True
    assert checkpoint_still_required("mail_send", "invoice attached") is not None
    pay = checkpoint_still_required("computer_click", "Place your order")
    assert pay is not None
    assert "Owner checkpoint" in pay


def test_v038_verification_not_exit_code_alone(tmp_path: Path):
    from remedy.verification.evidence import ActionResult, VerificationStatus
    from remedy.verification.verifier import verify_action

    p = tmp_path / "x.txt"
    assert (
        verify_action(ActionResult(tool="file_write", ok=True, path=str(p))).status
        == VerificationStatus.FAIL
    )


