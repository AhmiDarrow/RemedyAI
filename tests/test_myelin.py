"""Myelin — crystallized cognition: worn pathways become tested local skill."""

from __future__ import annotations

from remedy.memory.myelin import (
    CANDIDATE_MIN_USES,
    candidates,
    candidates_line,
    crystallize,
    list_sheaths,
    load_ledger,
    myelin_status,
    observe_pathway,
    run_sheath,
    stale_sheath,
    task_signature,
    verify_sheath,
)

GOOD_SCRIPT = """import sys
nums = [float(x) for x in sys.argv[1:]] or [0]
print(sum(nums) / len(nums))
"""
GOOD_TEST = """import subprocess, sys
r = subprocess.run([sys.executable, "run.py", "2", "4"], capture_output=True, text=True)
assert r.returncode == 0
assert abs(float(r.stdout.strip()) - 3.0) < 1e-9
print("ok")
"""
BAD_TEST = """raise SystemExit(1)
"""


# --- signatures ------------------------------------------------------------


def test_signature_extracts_verb_and_object():
    assert task_signature("please reconcile my card receipts again") == (
        "reconcile card receipts"
    )
    assert task_signature("Can you summarize the meeting notes") == (
        "summarize meeting notes"
    )


def test_signature_ignores_chat_and_secrets():
    assert task_signature("how are you today?") == ""
    assert task_signature("hi") == ""
    assert task_signature("check my api_key=sk-abcdefghij please") == ""


# --- pathway ledger --------------------------------------------------------


def test_repetition_builds_candidate(tmp_path):
    for _ in range(CANDIDATE_MIN_USES):
        sig = observe_pathway("reconcile my card receipts", tmp_path)
    assert sig == "reconcile card receipts"
    cands = candidates(tmp_path)
    assert cands and cands[0]["signature"] == sig
    assert cands[0]["count"] == CANDIDATE_MIN_USES
    line = candidates_line(tmp_path)
    assert "reconcile card receipts" in line and "myelin_crystallize" in line


def test_below_threshold_is_not_a_candidate(tmp_path):
    observe_pathway("reconcile my card receipts", tmp_path)
    assert candidates(tmp_path) == []
    assert candidates_line(tmp_path) == ""


def test_examples_are_scrubbed_and_bounded(tmp_path):
    for i in range(5):
        observe_pathway(f"reconcile my card receipts batch {i}", tmp_path)
    ledger = load_ledger(tmp_path)
    pw = ledger["pathways"]["reconcile card receipts"]
    assert len(pw["examples"]) <= 3


# --- crystallize / verify / run -------------------------------------------


def test_crystallize_green_is_verified(tmp_path):
    res = crystallize(
        name="Average numbers",
        description="Mean of argv numbers",
        script=GOOD_SCRIPT,
        test=GOOD_TEST,
        trigger="calculate average numbers",
        home=tmp_path,
    )
    assert res["ok"] and res["verified"] is True
    s = list_sheaths(tmp_path)[0]
    assert s.verified and s.slug == "average-numbers"


def test_crystallize_red_is_saved_unverified(tmp_path):
    res = crystallize(
        name="Broken", description="", script=GOOD_SCRIPT, test=BAD_TEST, home=tmp_path
    )
    assert res["ok"] and res["verified"] is False
    assert "FAILED" in res["note"]
    assert list_sheaths(tmp_path)[0].verified is False


def test_run_sheath_executes_locally(tmp_path):
    crystallize(
        name="avg", description="", script=GOOD_SCRIPT, test=GOOD_TEST, home=tmp_path
    )
    out = run_sheath("avg", ["10", "20"], tmp_path)
    assert out["ok"] is True
    assert abs(float(out["output"].strip()) - 15.0) < 1e-9
    assert out["uses"] == 1


def test_verify_sheath_updates_state(tmp_path):
    crystallize(
        name="avg", description="", script=GOOD_SCRIPT, test=GOOD_TEST, home=tmp_path
    )
    res = verify_sheath("avg", tmp_path)
    assert res["ok"] and res["verified"] is True


def test_covered_pathway_stops_being_candidate(tmp_path):
    for _ in range(CANDIDATE_MIN_USES):
        observe_pathway("calculate the average numbers today", tmp_path)
    assert candidates(tmp_path)
    crystallize(
        name="avg",
        description="",
        script=GOOD_SCRIPT,
        test=GOOD_TEST,
        trigger=candidates(tmp_path)[0]["signature"],
        home=tmp_path,
    )
    assert candidates(tmp_path) == []


def test_stale_sheath_flags_unverified_first(tmp_path):
    crystallize(
        name="bad", description="", script=GOOD_SCRIPT, test=BAD_TEST, home=tmp_path
    )
    s = stale_sheath(tmp_path)
    assert s is not None and s.slug == "bad"


def test_status_shape(tmp_path):
    crystallize(
        name="avg", description="mean", script=GOOD_SCRIPT, test=GOOD_TEST, home=tmp_path
    )
    st = myelin_status(tmp_path)
    assert st["verified"] == 1
    assert st["sheaths"][0]["slug"] == "avg"


# --- organism wiring -------------------------------------------------------


def test_turn_update_feeds_pathways(tmp_path):
    from remedy.memory.soul.field import clear_soul_cache
    from remedy.memory.soul.update import update_soul_after_turn

    clear_soul_cache()
    for _ in range(CANDIDATE_MIN_USES):
        update_soul_after_turn(
            user_text="reconcile my card receipts",
            assistant_text="Done.",
            session_id="my1",
            home=tmp_path,
        )
    assert candidates(tmp_path)
    clear_soul_cache()


def test_inject_carries_candidates_line(tmp_path):
    from remedy.memory.soul.field import clear_soul_cache
    from remedy.memory.soul.inject import build_soul_context_block
    from remedy.memory.soul.update import update_soul_after_turn

    clear_soul_cache()
    for _ in range(CANDIDATE_MIN_USES):
        update_soul_after_turn(
            user_text="reconcile my card receipts",
            assistant_text="Done.",
            session_id="my2",
            home=tmp_path,
        )
    block = build_soul_context_block(home=tmp_path)
    assert "Myelin candidates" in block
    clear_soul_cache()


def test_vigil_reverifies_at_night(tmp_path):
    from remedy.memory.soul.vigil import set_vigil_enabled, vigil_tick, wake_hungers

    crystallize(
        name="bad", description="", script=GOOD_SCRIPT, test=BAD_TEST, home=tmp_path
    )
    hungers = wake_hungers(tmp_path)
    assert any(h["act"] == "myelin_verify" for h in hungers)
    set_vigil_enabled(True, tmp_path)
    res = vigil_tick(tmp_path)
    assert res.get("woke") is True
    if res["act"] == "myelin_verify":
        assert res["detail"] == "bad"
