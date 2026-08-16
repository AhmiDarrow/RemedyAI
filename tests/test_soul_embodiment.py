"""Embodiment — evidence-based body choice, silent when there's no choice."""

from __future__ import annotations

from remedy.memory.soul.embodiment import (
    ballast_for,
    capability_score,
    choose_embodiment,
    embodiment_status,
)
from remedy.memory.soul.field import clear_soul_cache
from remedy.memory.soul.proprioception import muscle_correction_block, observe_render
from remedy.memory.soul.update import update_soul_after_turn

AMNESIA = (
    "As an AI language model, I don't have memory of previous conversations."
)
CLEAN = "Done — tests green, moving to the tray wiring."

FRONTIER = {"provider": "openai", "model": "gpt-5"}
LOCAL = {"provider": "ollama", "model": "qwen2.5-7b", "base_url": "http://127.0.0.1:11434"}


def _drift(candidate, home, n=4, text=AMNESIA):
    for _ in range(n):
        observe_render(
            assistant_text=text,
            provider=candidate["provider"],
            model=candidate["model"],
            home=home,
        )


def _clean(candidate, home, n=6):
    for _ in range(n):
        observe_render(
            assistant_text=CLEAN,
            provider=candidate["provider"],
            model=candidate["model"],
            home=home,
        )


# --- single-provider reality ----------------------------------------------


def test_zero_candidates_is_none(tmp_path):
    assert choose_embodiment([], home=tmp_path) is None
    assert choose_embodiment(None, home=tmp_path) is None


def test_solo_fast_path_is_silent(tmp_path):
    r = choose_embodiment([FRONTIER], moment="build", home=tmp_path)
    assert r is not None
    assert r.solo is True
    assert r.reason == ""  # nothing to narrate when there is no choice
    assert r.muscle == "openai/gpt-5"


def test_solo_low_fidelity_body_gets_dense_ballast(tmp_path):
    _drift(LOCAL, tmp_path, n=5)
    r = choose_embodiment([LOCAL], home=tmp_path)
    assert r.solo and r.ballast == "dense"
    block = muscle_correction_block(
        LOCAL["provider"], LOCAL["model"], home=tmp_path
    )
    assert "Proprioception" in block  # only-body still held to shape


# --- choice between bodies -------------------------------------------------


def test_build_moment_leans_capability(tmp_path):
    # Frontier drifts a bit; local renders cleanly. Build still wants muscle.
    _drift(FRONTIER, tmp_path, n=2, text="Sorry! I apologize. Sorry, my apologies.")
    _clean(LOCAL, tmp_path)
    r = choose_embodiment([FRONTIER, LOCAL], moment="build", home=tmp_path)
    assert r.muscle == "openai/gpt-5"
    assert r.reason  # choice between bodies is explainable


def test_companion_moment_can_prefer_the_true_body(tmp_path):
    # Frontier resets identity chronically; local is faithful.
    _drift(FRONTIER, tmp_path, n=8)
    _clean(LOCAL, tmp_path, n=8)
    r = choose_embodiment([FRONTIER, LOCAL], moment="companion", home=tmp_path)
    assert r.muscle == "ollama/qwen2.5-7b"


def test_same_bodies_different_moments_can_disagree(tmp_path):
    _drift(FRONTIER, tmp_path, n=8)
    _clean(LOCAL, tmp_path, n=8)
    build = choose_embodiment([FRONTIER, LOCAL], moment="build", home=tmp_path)
    companion = choose_embodiment(
        [FRONTIER, LOCAL], moment="companion", home=tmp_path
    )
    assert build.muscle != companion.muscle  # no one leaderboard fits all


# --- capability evidence ---------------------------------------------------


def test_capability_prior_orders_tiers(tmp_path):
    strong = capability_score("openai", "gpt-5", home=tmp_path)
    weak = capability_score("ollama", "tiny-1b", home=tmp_path)
    assert strong > weak


def test_observed_valence_pulls_capability(tmp_path):
    clear_soul_cache()
    base = capability_score("openai", "gpt-5", home=tmp_path)
    for _ in range(6):
        update_soul_after_turn(
            user_text="this is broken, why does it keep failing, fix it",
            assistant_text="Trying again.",
            session_id="cap",
            provider="openai",
            model="gpt-5",
            home=tmp_path,
        )
    clear_soul_cache()
    lowered = capability_score("openai", "gpt-5", home=tmp_path)
    assert lowered < base  # bad shared episodes cost the body trust as compute
    clear_soul_cache()


# --- misc ------------------------------------------------------------------


def test_ballast_thresholds():
    assert ballast_for(0.9) == "none"
    assert ballast_for(0.6) == "light"
    assert ballast_for(0.3) == "dense"


def test_status_ranked_and_safe(tmp_path):
    s = embodiment_status([FRONTIER, LOCAL], moment="build", home=tmp_path)
    assert s["bodies"][0]["score"] >= s["bodies"][1]["score"]
    assert not s["solo"]
    assert embodiment_status([], home=tmp_path)["bodies"] == []
    assert embodiment_status([LOCAL], home=tmp_path)["solo"] is True
