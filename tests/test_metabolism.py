"""Metabolism organs — tier, evidence, decision, map, shadow, IR, crystal, governor, verify, identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.core.metabolism.action_ir import start_action_ir
from remedy.core.metabolism.decision import get_decision_tracker, reset_decision_tracker
from remedy.core.metabolism.evidence import get_evidence_ledger, reset_evidence_ledger
from remedy.core.metabolism.governor import get_governor, reset_governor
from remedy.core.metabolism.identity_export import (
    build_identity_payload,
    export_identity,
    import_identity,
)
from remedy.core.metabolism.machine_map import get_machine_map, reset_machine_map
from remedy.core.metabolism.shadow import rehearse, should_shadow
from remedy.core.metabolism.tier import TurnTier, classify_turn_tier, tier_policy
from remedy.core.metabolism.time_crystal import get_time_crystal, reset_time_crystal
from remedy.core.metabolism.turn import (
    after_tool_batch,
    begin_turn_metabolism,
    end_turn_metabolism,
    metabolism_public_snapshot,
)
from remedy.core.metabolism.verify import verify_critical


@pytest.fixture(autouse=True)
def _clean_session():
    sid = "test_meta_sess"
    reset_evidence_ledger(sid)
    reset_decision_tracker(sid)
    reset_machine_map(sid)
    reset_time_crystal(sid)
    reset_governor(sid)
    yield
    reset_evidence_ledger(sid)
    reset_decision_tracker(sid)
    reset_machine_map(sid)
    reset_time_crystal(sid)
    reset_governor(sid)


def test_tier_l0_model_and_skills():
    assert classify_turn_tier("what model am I using?") == TurnTier.L0_INSTANT
    assert classify_turn_tier("list my skills") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what is your version") == TurnTier.L0_INSTANT


def test_tier_l3_autonomous_and_partition():
    assert classify_turn_tier("work alone and finish the suite") == TurnTier.L3_DEEP
    assert classify_turn_tier(
        "review auth and database modules in parallel across the codebase"
    ) == TurnTier.L3_DEEP


def test_tier_l2_agency():
    t = classify_turn_tier("implement file_edit for the bug in src/remedy/core/agent.py")
    assert t == TurnTier.L2_AGENCY
    pol = tier_policy(t)
    assert pol.allow_tools and pol.record_ir and pol.shadow_high_blast


def test_tier_l1_chat():
    assert classify_turn_tier("explain how hashing works briefly") == TurnTier.L1_LEAN


def test_evidence_ledger_admits_paths_and_dedupes():
    led = get_evidence_ledger("test_meta_sess")
    a = led.admit_tool_result(
        tool_name="file_read",
        content="ok\npath: C:\\Users\\Administrator\\RemedyAI\\src\\remedy\\core\\agent.py\n",
        success=True,
    )
    assert a
    n = led.evidence_units
    b = led.admit_tool_result(
        tool_name="file_read",
        content="ok\npath: C:\\Users\\Administrator\\RemedyAI\\src\\remedy\\core\\agent.py\n",
        success=True,
    )
    # path dedupe — may still get tool-summary difference; units not explode
    assert led.evidence_units <= n + 2
    assert led.snapshot()["evidence_units"] >= 1


def test_evidence_redacts_secrets():
    led = get_evidence_ledger("test_meta_sess")
    led.admit_tool_result(
        tool_name="bash_exec",
        content="api_key=sk-abcdefghijklmnopqrstuvwxyz token=bearer deadbeefdeadbeef",
        success=True,
    )
    snap = led.snapshot()
    blob = json.dumps(snap)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in blob
    assert "[redacted]" in blob or "api_key" in blob


def test_decision_waste_scoring():
    d = get_decision_tracker("test_meta_sess")
    d.record_tool_batch(new_eu=0)
    d.record_tool_batch(new_eu=2)
    s = d.snapshot()
    assert s["waste_tool_batches"] == 1
    assert s["productive_tool_batches"] == 1
    assert s["waste_batch_rate"] == 0.5


def test_machine_map_ttl_and_hint():
    m = get_machine_map("test_meta_sess")
    m.set_work_roots([r"C:\Users\Administrator\RemedyAI"])
    m.note_browser(url="https://mail.google.com", settled=True)
    hint = m.system_hint()
    assert "browser_url=" in hint
    assert "work_roots=" in hint


def test_shadow_blocks_destructive_shell():
    assert should_shadow("bash_exec", tier=2)
    r = rehearse("bash_exec", {"command": "rm -rf /"}, tier=2)
    assert r.blocked
    r2 = rehearse("bash_exec", {"command": "echo hi"}, tier=2)
    assert r2.outcome == "pass"
    assert not should_shadow("bash_exec", tier=0)


def test_shadow_blocks_path_outside_roots(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    r = rehearse(
        "file_write",
        {"path": str(tmp_path / "other" / "x.txt"), "content": "nope"},
        tier=2,
        work_roots=[str(root)],
    )
    assert r.blocked


def test_action_ir_redacts_and_persists(tmp_path: Path):
    ir = start_action_ir(session_id="test_meta_sess", tier=2, brief_head="fix bug")
    ir.add_step(
        tool="file_write",
        arguments={"path": "a.txt", "api_key": "sk-secretvalue123456"},
        result="ok",
        eu_delta=1,
    )
    pub = ir.to_public()
    assert pub["steps"][0]["args"]["api_key"] == "[redacted]"
    path = ir.persist(tmp_path)
    assert path is not None and path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "sk-secretvalue" not in json.dumps(data)


def test_time_crystal_never_promotes_secrets():
    tc = get_time_crystal("test_meta_sess")
    assert tc.admit("api_key=sk-abcdef0123456789", horizon="session") is None
    assert tc.blocked_secret >= 1
    f = tc.admit("Prefer TypeScript for new modules", horizon="session")
    assert f is not None
    f.hits = 3
    assert tc.promote_session_to_project(min_hits=2) >= 1
    assert any(x.horizon == "project_week" for x in tc.facts)


def test_governor_reacts_to_stuck():
    g = get_governor("test_meta_sess")
    d = g.observe_and_decide(
        quality={"stuck_rate": 0.2, "max_tool_fail_streak": 4, "turns": 5},
        metabolism={"waste_batch_rate": 0.5, "evidence_units": 0, "decision_units": 0},
        tier=2,
    )
    assert d.actions
    assert "recovery_remedy" in d.actions or "compress_earlier" in d.actions
    notes = g.system_notes()
    assert notes


def test_verify_false_green():
    r = verify_critical(
        assistant_text="All tests passed and we are done.",
        recent_tool_texts=["===== 3 failed, 2 passed in 1.2s ====="],
    )
    assert not r.ok
    assert r.silent_remedy


def test_verify_secret_risk():
    r = verify_critical(assistant_text="Here is api_key=sk-abcdefghijklmnop")
    assert not r.ok
    assert r.kind == "secret_risk"


def test_begin_and_after_tool_metabolism():
    meta = begin_turn_metabolism(
        session_id="test_meta_sess",
        user_text="implement fix in src/foo.py",
        intent="tool",
        tools_enabled=True,
    )
    assert meta["tier"] >= 2
    assert meta.get("injects") is not None
    out = after_tool_batch(
        session_id="test_meta_sess",
        tool_name="file_read",
        arguments={"path": r"C:\Users\Administrator\RemedyAI\src\foo.py"},
        content="line1\n",
        success=True,
        tier=2,
        action_ir=meta.get("action_ir"),
    )
    assert out["evidence_units"] >= 1
    end = end_turn_metabolism(
        session_id="test_meta_sess",
        action_ir=meta.get("action_ir"),
        status="done",
    )
    assert "metabolism" in end
    pub = metabolism_public_snapshot("test_meta_sess")
    assert "evidence" in pub and "governor" in pub


def test_identity_export_import_roundtrip(tmp_path: Path):
    payload = build_identity_payload(
        partner_memory=[{"text": "Prefers dark mode"}],
        time_crystal=[{"text": "Uses Windows", "horizon": "life"}],
        display_name="Ahmi",
    )
    # Ensure secrets cannot be smuggled
    payload["partner_memory"].append({"text": "api_key=sk-shouldnot", "token": "x"})
    clean = build_identity_payload(
        partner_memory=payload["partner_memory"],
        display_name="Ahmi",
    )
    path = export_identity(clean, tmp_path / "id.remedy", passphrase="test-pass-12")
    got = import_identity(path, passphrase="test-pass-12")
    assert got["display_name"] == "Ahmi"
    assert "api_keys" in (got.get("excludes") or [])
    with pytest.raises(ValueError):
        import_identity(path, passphrase="wrong-password-xx")


def test_spread_force_lowers_bar():
    from remedy.core.spread.planner import plan_spread

    weak = plan_spread("look at auth module please", intent="tool", force=False)
    strong = plan_spread(
        "look at auth and database modules across the codebase",
        intent="tool",
        force=True,
    )
    # force path should be more willing to spread when multi-area
    assert strong.spread or strong.score >= weak.score
