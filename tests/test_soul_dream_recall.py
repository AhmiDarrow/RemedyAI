"""Soul dream cycle + unified recall."""

from __future__ import annotations

import pytest

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


def test_dream_rehearses_and_keeps_salient_old_episode(tmp_path):
    # A pivotal old episode buried under trivial recent ones must survive the
    # dream's compression (old FIFO `episodes[-8:]` would have dropped it), and
    # spaced rehearsal must refresh at least one high-value trace.
    import time

    from remedy.memory.soul.field import EpisodeResidue

    clear_soul_cache()
    reset_dream_cooldown()
    sf = load_soul_field(tmp_path)
    now = time.time()
    day = 86400.0
    eps = []
    for i in range(12):
        eps.append(
            EpisodeResidue(
                id=f"d{i}",
                ts=now - (12 - i) * day,
                arc=f"trivial turn {i}",
                strength=0.1,
                last_recall_ts=now - (12 - i) * day,
            )
        )
    eps[0].id = "pivotal"
    eps[0].arc = "the pivotal launch decision"
    eps[0].strength = 0.95
    eps[0].open_thread = "ship the launch"
    eps[0].recalls = 4
    sf.episodes = eps
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    reset_dream_cooldown()

    result = dream_cycle(home=tmp_path, force=True, use_local=False)
    assert result.get("ok") and not result.get("skipped")
    assert result.get("rehearsed", 0) >= 1

    sf2 = load_soul_field(tmp_path)
    assert len(sf2.episodes) <= 8
    assert "pivotal" in {e.id for e in sf2.episodes}, (
        "salience-aware dream must keep the pivotal old episode, not FIFO-drop it"
    )


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


@pytest.mark.asyncio
async def test_recall_unified_async_runs_off_loop_and_times_out(tmp_path, monkeypatch):
    import asyncio

    from remedy.memory.soul import recall as recall_mod

    out = await recall_mod.recall_unified_async("tests", home=tmp_path, limit=5)
    assert isinstance(out, str) and out

    def _hang(*a, **k):
        import time

        time.sleep(2)
        return "late"

    monkeypatch.setattr(recall_mod, "recall_unified", _hang)
    t0 = asyncio.get_running_loop().time()
    out = await recall_mod.recall_unified_async("x", home=tmp_path, budget_s=0.2)
    assert "timed out" in out
    assert asyncio.get_running_loop().time() - t0 < 1.5
