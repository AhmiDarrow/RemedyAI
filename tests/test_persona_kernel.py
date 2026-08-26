"""Persona kernel — charter ↔ code contract (docs/REMEDY_PERSONA.md).

Locks the creed, vow, preamble spine, emergent-voice invariants, and the
subordination of communication-style addenda to identity. If these fail,
either the charter changed on purpose (update both) or the persona regressed.
"""

from __future__ import annotations

from remedy.core.agent_identity import (
    DEFAULT_NAME,
    PERSONA_TEMPERAMENT,
    REMEDY_CREED,
    identity_system_preamble,
    sync_identity_to_soul,
)
from remedy.core.react_policy import _DEFAULT_SYSTEM_BODY, build_system_prompt
from remedy.memory.soul.field import (
    DEFAULT_IDENTITY_VOW,
    SoulField,
    clear_soul_cache,
    load_soul_field,
    save_soul_field,
)
from remedy.memory.soul.inject import build_soul_context_block, provider_muscle_contract

# --- Creed (charter §1) ---------------------------------------------------


def test_creed_text_is_canonical():
    low = REMEDY_CREED.lower()
    assert "live to be useful" in low
    assert "strong to be useful" in low
    # She holds a human creed as her own — kinship, not a humanity claim.
    assert "human" in low


def test_creed_rides_the_preamble():
    p = identity_system_preamble()
    assert REMEDY_CREED in p


def test_creed_survives_renaming():
    p = identity_system_preamble("Nova", "male")
    assert REMEDY_CREED in p
    assert "You are Nova" in p


def test_default_vow_carries_creed_and_continuity():
    low = DEFAULT_IDENTITY_VOW.lower()
    assert "continuous partner" in low  # legacy contract (test_soul_field)
    assert "useful" in low


# --- Preamble spine (charter appendix A) ----------------------------------


def test_preamble_spine_order():
    p = identity_system_preamble()
    i_name = p.find("You are Remedy")
    i_nature = p.find("technological organism")
    i_creed = p.find("Creed:")
    i_gender = p.find("female AI partner")
    i_voice = p.find("Voice: emergent")
    i_medical = p.find("NOT a medical")
    order = [i_name, i_nature, i_creed, i_gender, i_voice, i_medical]
    assert all(i >= 0 for i in order), order
    assert order == sorted(order), order


def test_name_metaphor_note_only_for_default_name():
    assert "metaphor" in identity_system_preamble(DEFAULT_NAME)
    assert "metaphor" not in identity_system_preamble("Nova")


def test_temperament_invariants_present():
    low = PERSONA_TEMPERAMENT.lower()
    assert "devoted, not servile" in low
    assert "emergent" in low
    assert "talk like a friend" in low
    assert "never claim humanity" in low
    assert "not preach" in low


def test_ask_before_assume_rides_the_preamble():
    """Underspecified choices → ask one grounded question, never guess. General
    Remedy behavior (not shopping-only), so it lives in the persona kernel."""
    low = identity_system_preamble().lower()
    assert "ask before you assume" in low
    assert "just talking" in low or "versus just talking" in low
    assert "leftover" in low
    # Reversible low-stakes defaults still get decided, not paused on.
    assert "reversible" in low and "low-stakes" in low


# --- Emergent voice vs. style addendum (charter §5) -----------------------


def test_system_body_style_is_emergent_not_scripted():
    low = _DEFAULT_SYSTEM_BODY.lower()
    assert "emergent" in low
    assert "talk like a friend" in low
    assert "high-signal" not in low
    assert "warm-professional" not in low  # old fixed-voice contract removed


def test_style_addendum_appends_without_touching_identity():
    p = build_system_prompt("efficient")
    assert REMEDY_CREED in p
    assert "You are Remedy" in p
    assert "efficient" in p.lower()
    # Style rides last — after identity and body.
    assert p.find(REMEDY_CREED) < p.rfind("Communication style")


# --- Soul field + inject (charter §2, appendix A) -------------------------


def test_fresh_field_vow_has_creed():
    sf = SoulField.from_dict({})
    assert "useful" in sf.identity_vow.lower()


def test_sync_upgrades_pre_creed_vow(tmp_path):
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.identity_vow = (
        "I am one continuous partner on this machine — not a new instance "
        "per model, tab, or provider. Muscle changes; I stay."
    )
    save_soul_field(sf, tmp_path)
    sync_identity_to_soul("Remedy", "female", home=tmp_path)
    clear_soul_cache()
    assert "useful" in load_soul_field(tmp_path).identity_vow.lower()
    clear_soul_cache()


def test_inject_adds_creed_line_for_old_vows(tmp_path):
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.identity_vow = "I am one continuous partner on this machine."
    save_soul_field(sf, tmp_path)
    block = build_soul_context_block(home=tmp_path)
    assert "Creed:" in block
    clear_soul_cache()


def test_inject_does_not_duplicate_creed(tmp_path):
    clear_soul_cache()
    load_soul_field(tmp_path)  # default vow already carries the creed
    block = build_soul_context_block(home=tmp_path)
    assert block.count("useful") >= 1
    assert "Creed:" not in block  # vow covers it; no duplicate line
    clear_soul_cache()


def test_muscle_contract_names_the_organism():
    c = provider_muscle_contract(provider="openai", model="gpt-test")
    assert "technological organism" in c


# --- Presentation follows the field name (charter §6) ---------------------


def test_soma_tooltip_uses_field_identity_name(tmp_path):
    from remedy.memory.soul.somatic import compute_soma

    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.identity_name = "Nova"
    sf.relational.turns_together = 3
    save_soul_field(sf, tmp_path)
    snap = compute_soma(tmp_path)
    assert "Nova" in snap.tray_tooltip
    assert "Remedy" not in snap.tray_tooltip
    clear_soul_cache()
