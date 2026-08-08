"""Organism pulse — living partner organs produce real inject text."""

from __future__ import annotations

from pathlib import Path

from remedy.core.metabolism.organism import (
    forge_pulse,
    immune_pulse,
    organism_pulse_block,
)
from remedy.core.metabolism.turn import begin_turn_metabolism
from remedy.memory.soul.field import SoulField, save_soul_field
from remedy.memory.soul.update import update_soul_after_turn


def test_organism_pulse_after_soul_update(tmp_path: Path) -> None:
    home = tmp_path / "h"
    home.mkdir()
    update_soul_after_turn(
        user_text="implement the login form and fix the bug",
        assistant_text="I'll open the files and write the form.",
        session_id="s1",
        provider="xai",
        model="grok-4",
        home=home,
    )
    block = organism_pulse_block(
        session_id="s1",
        tier=2,
        home=home,
        user_text="implement the login form and fix the bug",
        project_path=str(tmp_path / "proj"),
        max_chars=1200,
    )
    assert block
    assert "Organism" in block or "alive" in block or "Forge" in block
    assert "rapport" in block or "Forge" in block


def test_forge_pulse_on_build_intent() -> None:
    class _R:
        _llm_provider = "xai"
        _llm_model = "grok-4"
        _llm_base_url = ""

    out = forge_pulse(
        user_text="build a calculator and ship it",
        tier=2,
        runtime=_R(),
        session_id="s",
    )
    assert out
    assert "Forge" in out


def test_immune_pulse_when_verify_flagged() -> None:
    from remedy.core.metabolism.governor import get_governor, reset_governor

    reset_governor("imm1")
    g = get_governor("imm1")
    g.observe_and_decide(
        quality={"stuck_rate": 0.2, "max_tool_fail_streak": 3, "turns": 4},
        metabolism={"evidence_units": 1, "decision_units": 1, "waste_batch_rate": 0.1},
        tier=2,
    )
    out = immune_pulse(tier=2, session_id="imm1", gov_actions=g.last_actions)
    assert out
    assert "Immune" in out


def test_begin_turn_includes_organism_inject(tmp_path: Path) -> None:
    home = tmp_path / "h2"
    home.mkdir()
    sf = SoulField()
    sf.identity_name = "Remedy"
    sf.relational.turns_together = 5
    sf.relational.rapport = 0.7
    save_soul_field(sf, home)

    class _R:
        _llm_provider = "anthropic"
        _llm_model = "claude-sonnet-4"
        config = type("C", (), {"home_dir": str(home)})()

    meta = begin_turn_metabolism(
        session_id="s2",
        user_text="fix the failing tests and implement the retry path",
        tools_enabled=True,
        pre_tier=2,
        runtime=_R(),
        home=home,
        project_path=str(tmp_path / "p"),
    )
    injects = "\n".join(meta.get("injects") or [])
    # Organism pulse and/or governor/tier notes should land for L2
    assert injects
    assert any(
        k in injects
        for k in ("Organism", "Forge", "Governor", "Metabolism", "tier", "L2", "L3")
    )
