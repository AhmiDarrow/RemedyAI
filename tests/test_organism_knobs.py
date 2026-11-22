"""Advanced organism knobs: defaults stay put, overrides and env vars work."""

from __future__ import annotations

from pathlib import Path

from remedy.core import organism_knobs as knobs
from remedy.memory.soul.inject import DEFAULT_SOUL_CHARS


def test_defaults_match_production_constants() -> None:
    table = knobs.effective_table({})
    assert table["improve_tick_s"] == 60
    assert table["improve_tick_min_s"] == 20
    assert table["improve_tick_max_s"] == 900
    assert table["improve_heavy_window_s"] == 300
    assert table["turn_end_timeout_s"] == 30
    assert table["after_tool_batch_timeout_s"] == 10
    assert table["pulse_learn_timeout_s"] == 45
    assert table["soul_inject_chars"] == DEFAULT_SOUL_CHARS == 1400
    assert table["max_result_chars"] == 24_000
    assert table["soul_max_bytes"] == 64 * 1024
    assert table["residue_max_bytes"] == 256 * 1024
    assert table["journal_max_bytes"] == 64 * 1024
    assert table["learn_log_max_bytes"] == 256 * 1024
    assert table["learn_log_max_lines"] == 400
    assert table["max_episodes"] == 12
    assert table["max_future_dreams"] == 8
    assert table["max_phenotypes"] == 128
    assert table["vigil_offer_turns"] == 12
    assert table["dream_cooldown_s"] == 120.0
    assert table["organism_compact_every_s"] == 86_400.0
    assert table["organism_stall_after_s"] == 7 * 86_400.0
    assert table["voice_evolution"] is True
    assert table["self_inject_idle_s"] == 300


def test_advanced_table_overrides_take_effect() -> None:
    cfg = {"advanced": {"soul_inject_chars": 800, "improve_tick_s": 120}}
    assert knobs.resolve_int("soul_inject_chars", cfg) == 800
    assert knobs.resolve_int("improve_tick_s", cfg) == 120
    # Unmentioned keys stay at defaults.
    assert knobs.resolve_int("max_episodes", cfg) == 12


def test_overrides_are_clamped_and_unknown_keys_dropped() -> None:
    out = knobs.normalize_table(
        {"soul_inject_chars": 50, "improve_tick_s": 99999, "not_a_knob": 1}
    )
    assert "not_a_knob" not in out
    assert out["soul_inject_chars"] == 200  # minimum
    assert out["improve_tick_s"] == 3600  # maximum
    # A value equal to the default is not persisted.
    assert knobs.normalize_table({"improve_tick_s": 60}) == {}


def test_react_max_result_env_wins(monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_REACT_MAX_RESULT_CHARS", "8000")
    cfg = {"advanced": {"max_result_chars": 12_000}}
    assert knobs.resolve_int("max_result_chars", cfg) == 8000
    monkeypatch.delenv("REMEDY_REACT_MAX_RESULT_CHARS")
    assert knobs.resolve_int("max_result_chars", cfg) == 12_000


def test_no_voice_evolution_env_still_disables(monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_NO_VOICE_EVOLUTION", "1")
    cfg = {"advanced": {"voice_evolution": True}}
    assert knobs.resolve_bool("voice_evolution", cfg) is False
    monkeypatch.delenv("REMEDY_NO_VOICE_EVOLUTION")
    assert knobs.resolve_bool("voice_evolution", cfg) is True
    cfg = {"advanced": {"voice_evolution": False}}
    assert knobs.resolve_bool("voice_evolution", cfg) is False


def test_legacy_self_inject_idle_seconds_maps() -> None:
    cfg = {"self_inject": {"idle_seconds": 600}}
    assert knobs.resolve_int("self_inject_idle_s", cfg) == 600
    # [advanced] wins over the legacy table when both are set.
    cfg["advanced"] = {"self_inject_idle_s": 120}
    assert knobs.resolve_int("self_inject_idle_s", cfg) == 120



def test_schema_covers_every_knob() -> None:
    keys = {item["key"] for item in knobs.schema()}
    assert keys == set(knobs.KNOB_BY_KEY)
    assert "soul_inject_chars" in keys
    assert "max_result_chars" in keys
