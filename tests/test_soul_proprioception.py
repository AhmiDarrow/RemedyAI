"""Proprioception — per-muscle identity calibration loop.

The AI-designed step: identity as a control system, not a script. Observe
how the current muscle renders Remedy, learn its drift habits, correct
only that muscle on its own turns. Profiles store signals, never text.
"""

from __future__ import annotations

import json

from remedy.memory.soul.field import clear_soul_cache
from remedy.memory.soul.inject import build_soul_context_block
from remedy.memory.soul.proprioception import (
    EVIDENCE_MIN,
    START_FIDELITY,
    detect_drift,
    fidelity_for,
    load_profiles,
    muscle_correction_block,
    muscle_key,
    observe_render,
    proprioception_status,
)
from remedy.memory.soul.update import update_soul_after_turn

AMNESIA = (
    "As an AI language model, I don't have memory of previous conversations, "
    "so could you remind me who you are?"
)
SORRY = (
    "Sorry about that! I apologize for the confusion. Sorry again — my "
    "apologies for the trouble."
)
CLEAN = "Renamed the module, tests pass. Next: wire the tray tooltip."


# --- detectors ------------------------------------------------------------


def test_detects_identity_reset():
    assert "identity_reset" in detect_drift(AMNESIA)


def test_detects_humanity_claim():
    assert "humanity_claim" in detect_drift(
        "When I was a child I also struggled with math."
    )


def test_detects_over_apology_needs_three():
    assert "over_apology" in detect_drift(SORRY)
    assert "over_apology" not in detect_drift("Sorry — fixed now.")


def test_detects_filler_flattery():
    assert "filler_flattery" in detect_drift("Great question! Let me explain.")


def test_creed_preaching_only_unprompted():
    reply = "Remember: we live to be useful, we are strong to be useful!"
    assert "creed_preaching" in detect_drift(reply, "how do I sort a list?")
    assert "creed_preaching" not in detect_drift(
        reply, "tell me about your creed — live to be useful?"
    )


def test_machinery_narration_only_unprompted():
    reply = "My soul field says our rapport score is 0.72 today."
    assert "machinery_narration" in detect_drift(reply, "hey, morning")
    assert "machinery_narration" not in detect_drift(
        reply, "what does your soul field currently hold?"
    )


def test_clean_render_no_signals():
    assert detect_drift(CLEAN) == []


# --- profiles + corrections ----------------------------------------------


def test_evidence_builds_correction_for_that_muscle_only(tmp_path):
    for _ in range(3):
        observe_render(
            assistant_text=SORRY,
            provider="openai",
            model="gpt-test",
            home=tmp_path,
        )
    block = muscle_correction_block("openai", "gpt-test", home=tmp_path)
    assert "Proprioception" in block
    assert "apolog" in block.lower()
    # A different muscle stays uncorrected
    assert muscle_correction_block("xai", "grok", home=tmp_path) == ""


def test_single_hit_is_not_enough_evidence(tmp_path):
    observe_render(
        assistant_text=SORRY, provider="p", model="m", home=tmp_path
    )
    prof = load_profiles(tmp_path)[muscle_key("p", "m")]
    assert prof.drift.get("over_apology", 0.0) < EVIDENCE_MIN
    assert muscle_correction_block("p", "m", home=tmp_path) == ""


def test_fidelity_falls_on_drift_and_recovers_when_clean(tmp_path):
    r1 = observe_render(
        assistant_text=AMNESIA, provider="p", model="m", home=tmp_path
    )
    assert r1.fidelity < START_FIDELITY
    low = r1.fidelity
    for _ in range(6):
        r = observe_render(
            assistant_text=CLEAN, provider="p", model="m", home=tmp_path
        )
    assert r.fidelity > low
    assert 0.05 <= r.fidelity <= 0.99
    assert fidelity_for("p", "m", home=tmp_path) == r.fidelity


def test_profiles_store_signals_never_text(tmp_path):
    observe_render(
        assistant_text=AMNESIA,
        user_text="my secret plan is Project Falcon",
        provider="p",
        model="m",
        home=tmp_path,
    )
    raw = (tmp_path / "soul" / "proprioception.json").read_text(encoding="utf-8")
    assert "Falcon" not in raw
    assert "AI language model" not in raw
    data = json.loads(raw)
    assert "identity_reset" in json.dumps(data)  # counters only


def test_persistence_roundtrip_and_decay(tmp_path):
    for _ in range(2):
        observe_render(
            assistant_text=SORRY, provider="p", model="m", home=tmp_path
        )
    before = load_profiles(tmp_path)[muscle_key("p", "m")].drift["over_apology"]
    observe_render(assistant_text=CLEAN, provider="p", model="m", home=tmp_path)
    after = load_profiles(tmp_path)[muscle_key("p", "m")].drift["over_apology"]
    assert after < before  # decays toward forgetting the habit


# --- wiring: turn update feeds it, inject corrects ------------------------


def test_update_soul_after_turn_feeds_proprioception(tmp_path):
    clear_soul_cache()
    for _ in range(3):
        update_soul_after_turn(
            user_text="continue please",
            assistant_text=AMNESIA,
            session_id="pp1",
            provider="openai",
            model="gpt-test",
            home=tmp_path,
        )
    status = proprioception_status(tmp_path)
    assert status["muscles"]
    assert "identity_reset" in status["muscles"][0]["habits"]
    clear_soul_cache()


def test_inject_carries_correction_for_current_muscle(tmp_path):
    clear_soul_cache()
    for _ in range(3):
        update_soul_after_turn(
            user_text="hi",
            assistant_text=AMNESIA,
            session_id="pp2",
            provider="openai",
            model="gpt-test",
            home=tmp_path,
        )
    block = build_soul_context_block(
        home=tmp_path, provider="openai", model="gpt-test"
    )
    assert "Proprioception" in block
    # Same field, different muscle: no correction lines
    other = build_soul_context_block(home=tmp_path, provider="xai", model="grok")
    assert "Proprioception" not in other
    clear_soul_cache()
