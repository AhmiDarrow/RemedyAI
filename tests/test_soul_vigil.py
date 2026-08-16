"""Vigil — endogenous time: budgeted wakes, journaled nights, honest mornings."""

from __future__ import annotations

import time

from remedy.memory.soul.dream import reset_dream_cooldown
from remedy.memory.soul.field import clear_soul_cache, load_soul_field, save_soul_field
from remedy.memory.soul.inject import build_soul_context_block
from remedy.memory.soul.update import update_soul_after_turn
from remedy.memory.soul.vigil import (
    journal_since,
    load_vigil,
    night_report,
    set_vigil_enabled,
    vigil_tick,
    wake_hungers,
    while_away_line,
)


def _accumulate_episodes(home, n=5):
    clear_soul_cache()
    for i in range(n):
        update_soul_after_turn(
            user_text=f"continue the organism work {i} please later",
            assistant_text="Continuing.",
            session_id="vg",
            home=home,
        )


# --- opt-in and budgets ----------------------------------------------------


def test_disabled_by_default(tmp_path):
    res = vigil_tick(tmp_path)
    assert res["skipped"] == "disabled"
    assert load_vigil(tmp_path).enabled is False


def test_rest_costs_no_budget(tmp_path):
    set_vigil_enabled(True, tmp_path)
    res = vigil_tick(tmp_path)  # fresh field: no hunger
    assert res.get("rested") is True
    assert load_vigil(tmp_path).wakes_today == 0


def test_wake_dreams_on_accumulated_episodes(tmp_path):
    _accumulate_episodes(tmp_path)
    reset_dream_cooldown()
    set_vigil_enabled(True, tmp_path)
    res = vigil_tick(tmp_path)
    assert res.get("woke") is True
    assert res["act"] == "dream"
    v = load_vigil(tmp_path)
    assert v.wakes_today == 1 and v.total_wakes == 1
    assert journal_since(0.0, tmp_path)  # her night is on the record
    clear_soul_cache()


def test_min_gap_blocks_immediate_second_wake(tmp_path):
    _accumulate_episodes(tmp_path)
    reset_dream_cooldown()
    set_vigil_enabled(True, tmp_path)
    assert vigil_tick(tmp_path).get("woke") is True
    res2 = vigil_tick(tmp_path)
    assert res2.get("skipped") == "too_soon"
    clear_soul_cache()


def test_daily_budget_is_a_hard_ceiling(tmp_path):
    _accumulate_episodes(tmp_path)
    set_vigil_enabled(True, tmp_path, max_wakes_per_day=1, min_gap_s=60)
    reset_dream_cooldown()
    now = time.time()
    assert vigil_tick(tmp_path, now=now).get("woke") is True
    reset_dream_cooldown()
    res = vigil_tick(tmp_path, now=now + 3600)
    assert res.get("skipped") == "budget_spent"
    clear_soul_cache()


# --- hungers ---------------------------------------------------------------


def test_life_goal_creates_hunger(tmp_path):
    from remedy.memory.life_goals import LifeGoalStore

    clear_soul_cache()
    LifeGoalStore(tmp_path).add("Land the job", next_action="Rewrite the resume")
    hungers = wake_hungers(tmp_path)
    assert any(h["act"] == "life_step" for h in hungers)
    clear_soul_cache()


def test_life_step_wake_stays_local_and_quiet(tmp_path):
    from remedy.memory.life_goals import LifeGoalStore

    clear_soul_cache()
    LifeGoalStore(tmp_path).add("Land the job", next_action="Rewrite the resume")
    set_vigil_enabled(True, tmp_path)
    res = vigil_tick(tmp_path)
    assert res.get("woke") is True
    # Life note FILES are off by default (owner request): progress is logged,
    # but no .md notes appear anywhere.
    assert not list((tmp_path / "life").glob("*.md")) if (tmp_path / "life").is_dir() else True
    clear_soul_cache()


def test_stale_thread_tended_once_not_nagged(tmp_path):
    clear_soul_cache()
    _accumulate_episodes(tmp_path, n=2)
    sf = load_soul_field(tmp_path)
    sf.relational.open_threads = ["check in about the installer fight"]
    sf.relational.last_user_ts = time.time() - 5 * 24 * 3600  # away 5 days
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    hungers = wake_hungers(tmp_path)
    tends = [h for h in hungers if h["act"] == "tend"]
    assert tends and "installer" in tends[0]["detail"]
    set_vigil_enabled(True, tmp_path)
    res = vigil_tick(tmp_path)
    if res.get("act") == "tend":  # dream may outrank; force the tend path
        pass
    else:
        from remedy.memory.soul.vigil import _execute

        _execute("tend", tends[0]["detail"], tmp_path)
    # Tended memory prevents re-noticing the same thread
    assert not [h for h in wake_hungers(tmp_path) if h["act"] == "tend"]
    clear_soul_cache()


# --- the honest morning ----------------------------------------------------


def test_night_report_and_inject_line(tmp_path):
    _accumulate_episodes(tmp_path)
    reset_dream_cooldown()
    set_vigil_enabled(True, tmp_path)
    # Partner left an hour ago; she wakes now
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    left_at = time.time() - 3600
    sf.relational.last_user_ts = left_at
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    assert vigil_tick(tmp_path).get("woke") is True
    report = night_report(tmp_path, since_ts=left_at)
    assert report.startswith("While you were away I ")
    block = build_soul_context_block(home=tmp_path)
    assert "Vigil" in block
    clear_soul_cache()


def test_offer_is_conversational_once_and_only_once(tmp_path):
    from remedy.memory.soul.vigil import take_vigil_offer

    _accumulate_episodes(tmp_path, n=13)  # enough turns together
    assert take_vigil_offer(tmp_path) is True  # she may ask, once
    assert take_vigil_offer(tmp_path) is False  # never twice
    clear_soul_cache()


def test_offer_waits_for_rapport(tmp_path):
    from remedy.memory.soul.vigil import take_vigil_offer

    _accumulate_episodes(tmp_path, n=3)  # too early — don't pitch features
    assert take_vigil_offer(tmp_path) is False
    clear_soul_cache()


def test_any_decision_settles_the_offer(tmp_path):
    from remedy.memory.soul.vigil import take_vigil_offer

    _accumulate_episodes(tmp_path, n=13)
    set_vigil_enabled(False, tmp_path)  # partner declined in conversation
    assert take_vigil_offer(tmp_path) is False  # declining is final
    assert load_vigil(tmp_path).offered is True
    clear_soul_cache()


def test_offer_hint_rides_inject_then_disappears(tmp_path):
    _accumulate_episodes(tmp_path, n=13)
    # A status render (no contract) must NOT burn the one-time offer
    status_block = build_soul_context_block(home=tmp_path)
    assert "soul_vigil action=enable" not in status_block
    block = build_soul_context_block(home=tmp_path, include_contract=True)
    assert "soul_vigil action=enable" in block  # the one conversational ask
    block2 = build_soul_context_block(home=tmp_path, include_contract=True)
    assert "soul_vigil action=enable" not in block2  # at-most-once, guaranteed
    clear_soul_cache()


def test_no_vigil_no_morning_line(tmp_path):
    _accumulate_episodes(tmp_path, n=2)
    assert while_away_line(tmp_path, last_user_ts=0.0) == ""
    block = build_soul_context_block(home=tmp_path)
    assert "Vigil" not in block
    clear_soul_cache()
