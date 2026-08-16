"""Soul dream cycle + unified recall."""

from __future__ import annotations

from remedy.memory.soul.dream import dream_cycle, reset_dream_cooldown
from remedy.memory.soul.field import clear_soul_cache, load_soul_field, save_soul_field
from remedy.memory.soul.recall import recall_unified
from remedy.memory.soul.update import update_soul_after_turn


def test_dream_promotes_repeated_threads(tmp_path):
    clear_soul_cache()
    reset_dream_cooldown()
    for i in range(5):
        update_soul_after_turn(
            user_text=f"Continue the soul field work please pass {i}. Don't forget later.",
            assistant_text="Working on soul field.",
            session_id="dream-1",
            provider="xai",
            model="grok-4",
            home=tmp_path,
        )
    # Force similar open threads via brief-less path — ensure episodes exist
    sf = load_soul_field(tmp_path)
    assert len(sf.episodes) >= 3
    # Manually stamp repeated open threads on episodes for promotion
    for ep in sf.episodes:
        ep.open_thread = "finish soul field organism"
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    reset_dream_cooldown()
    result = dream_cycle(home=tmp_path, force=True)
    assert result.get("ok")
    assert not result.get("skipped")
    sf2 = load_soul_field(tmp_path)
    assert any("soul field" in t.lower() for t in sf2.relational.open_threads) or sf2.pledges


def test_recall_finds_pledge(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="From now on we always ship with tests green.",
        assistant_text="Understood — tests green before ship.",
        session_id="rec-1",
        home=tmp_path,
    )
    out = recall_unified("tests green", home=tmp_path, limit=8)
    assert "recall" in out.lower() or "pledge" in out.lower() or "tests" in out.lower()
