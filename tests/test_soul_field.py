"""Soul Field — provider-invariant personhood layer."""

from __future__ import annotations

from remedy.memory.soul.field import (
    SoulField,
    clear_soul_cache,
    load_soul_field,
    save_soul_field,
)
from remedy.memory.soul.inject import build_soul_context_block, provider_muscle_contract
from remedy.memory.soul.update import record_self_inject_lesson, update_soul_after_turn


def test_muscle_contract_mentions_continuity():
    c = provider_muscle_contract(provider="xai", model="grok")
    assert "Soul" in c or "soul" in c.lower()
    assert "muscle" in c.lower()
    assert "xai" in c.lower()


def test_roundtrip_persist(tmp_path):
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.relational.help_mode = "pair"
    sf.pledges.append("always be honest about uncertainty")
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    sf2 = load_soul_field(tmp_path)
    assert sf2.relational.help_mode == "pair"
    assert any("honest" in p for p in sf2.pledges)


def test_update_creates_episode_residue(tmp_path):
    clear_soul_cache()
    sf = update_soul_after_turn(
        user_text="Let's implement the soul field and ship it.",
        assistant_text="Implemented the soul package and wired inject.",
        session_id="s-soul-1",
        provider="openai",
        model="gpt-test",
        home=tmp_path,
    )
    assert sf.relational.turns_together >= 1
    assert sf.episodes
    assert "ship" in " ".join(sf.relational.voice_markers).lower() or sf.episodes[-1].arc
    assert sf.episodes[-1].muscle.startswith("openai")


def test_frustrated_stance_and_correction(tmp_path):
    clear_soul_cache()
    sf = update_soul_after_turn(
        user_text="No, that's wrong — just fix it, no fluff.",
        assistant_text="Fixed.",
        session_id="s2",
        home=tmp_path,
    )
    assert sf.episodes[-1].user_stance == "frustrated"
    assert sf.relational.correction_style in ("blunt", "direct")


def test_inject_block_includes_residue(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="Continue the soul work please.",
        assistant_text="Continuing.",
        session_id="s3",
        home=tmp_path,
    )
    block = build_soul_context_block(
        home=tmp_path,
        include_contract=True,
        provider="anthropic",
        user_name="Ahmi",
    )
    assert "Soul Field" in block or "Soul / muscle" in block
    assert "Ahmi" in block
    assert "Episode residue" in block or "Bond:" in block


def test_self_inject_lesson_red(tmp_path):
    clear_soul_cache()
    sf = record_self_inject_lesson(
        outcome="rolled_back",
        tree="python",
        summary="pytest failed",
        round_id="r1",
        gate_detail="FAILED tests/test_x.py",
        home=tmp_path,
    )
    assert sf.organism_lessons
    assert sf.organism_lessons[-1].outcome == "rolled_back"
    assert "Gate failed" in sf.organism_lessons[-1].lesson
    block = build_soul_context_block(home=tmp_path)
    assert "Organism self-lessons" in block or "self-lessons" in block.lower()


def test_secret_redacted_from_residue(tmp_path):
    clear_soul_cache()
    sf = update_soul_after_turn(
        user_text="remember my api_key=sk-abcdefghijklmnopqrstuv",
        assistant_text="I will not store that.",
        session_id="s4",
        home=tmp_path,
    )
    blob = " ".join(e.arc for e in sf.episodes)
    assert "sk-abcdefghijklmnop" not in blob


def test_from_dict_defaults():
    sf = SoulField.from_dict({})
    assert sf.identity_name == "Remedy"
    assert "continuous partner" in sf.identity_vow.lower()
