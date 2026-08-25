"""v0.38 gates — capability auth, credentials, isolation, evidence, explain.

Documents the Optimization/Stability close-out:

- capability / policy still authorize tools (committed PolicyEngine)
- generic ``child_environment()`` must not inherit ambient ``GH_TOKEN``
- web facts are TOOL_OBSERVED, never USER_DECLARED
- hive workers cannot exceed parent caps (if ``hive_caps`` is importable)
- AUTONOMOUS does not waive mail_send / payment owner checkpoints
- ``explain_turn`` summarizes what / why / verified / remains
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.context import TurnFactory
from remedy.core.explain import explain_turn
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


def test_v038_explain_turn_fake_bus():
    # EventBus is owned by another agent; duck-type the for_turn contract.
    bus = SimpleNamespace(
        for_turn=lambda _turn_id: [
            SimpleNamespace(event_type="ToolCompleted", payload={"tool": "file_read"}),
            SimpleNamespace(
                event_type="VerificationCompleted", payload={"reason": "file exists"}
            ),
            SimpleNamespace(event_type="ApprovalRequested", payload={"reason": "mail"}),
            SimpleNamespace(event_type="GoalFailed", payload={"reason": "blocked"}),
        ]
    )
    summary = explain_turn(bus, "t")
    assert "file_read" in summary["what"]
    assert "file exists" in summary["verified"]
    assert "mail" in summary["why"]
    assert "blocked" in summary["remains"]


def test_v038_events_and_explain(tmp_path: Path):
    try:
        from remedy.events.bus import EventBus
        from remedy.events.types import EventType
    except ImportError:
        # events package is owned by another agent.
        pytest.skip("EventBus not importable yet")
    bus = EventBus(db_path=tmp_path / "e.db")
    bus.emit_simple(EventType.TOOL_COMPLETED, session_id="s", turn_id="t", tool="file_read")
    bus.emit_simple(
        EventType.VERIFICATION_COMPLETED, session_id="s", turn_id="t", reason="file exists"
    )
    summary = explain_turn(bus, "t")
    assert "file_read" in summary["what"]
    assert "file exists" in summary["verified"]


def test_v038_hive_caps_if_importable():
    try:
        from remedy.core.hive_caps import child_capabilities
    except ImportError:
        # hive_caps is owned by another agent.
        pytest.skip("hive_caps not importable yet")
    parent = frozenset({Capability.FS_READ})
    assert child_capabilities(parent, frozenset({Capability.FS_READ})) == parent


def test_v038_verification_not_exit_code_alone(tmp_path: Path):
    try:
        from remedy.verification.evidence import ActionResult, VerificationStatus
        from remedy.verification.verifier import verify_action
    except ImportError:
        # verification is owned by another agent.
        pytest.skip("verification not importable yet")
    p = tmp_path / "x.txt"
    assert (
        verify_action(ActionResult(tool="file_write", ok=True, path=str(p))).status
        == VerificationStatus.FAIL
    )


def test_v038_no_skip_verify():
    try:
        from remedy.execution.action import ActionRecord, ActionState, IllegalTransition
    except ImportError:
        # action.py is owned by another agent.
        pytest.skip("ActionRecord not importable yet")
    rec = ActionRecord(tool="host_run")
    rec.advance(ActionState.AUTHORIZED)
    rec.advance(ActionState.RUNNING)
    try:
        rec.advance(ActionState.COMPLETED)
        raise AssertionError("should have raised")
    except IllegalTransition:
        pass
