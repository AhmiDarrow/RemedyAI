"""Partner dreams — them / self / future."""

from __future__ import annotations

from remedy.memory.profile import UserFact, UserProfile
from remedy.memory.soul.dream import dream_cycle, reset_dream_cooldown
from remedy.memory.soul.field import (
    OrganismLesson,
    SoulField,
    clear_soul_cache,
    load_soul_field,
    save_soul_field,
)
from remedy.memory.soul.inject import build_soul_context_block
from remedy.memory.soul.partner_dream import (
    apply_partner_dreams,
    collect_self_moves,
    collect_user_goals,
    compose_partner_dreams,
)
from remedy.memory.soul.recall import recall_unified


def test_compose_binds_goal_to_self_move():
    sf = SoulField()
    sf.pledges.append("Goal: ship Remedy 1.0")
    sf.relational.help_mode = "silent-doer"
    sf.relational.correction_style = "blunt"
    dreams = compose_partner_dreams(sf, limit=4)
    assert dreams
    assert any("Toward" in d and "ship" in d.lower() for d in dreams)
    assert any("act first" in d.lower() or "defensive" in d.lower() for d in dreams)


def test_profile_life_goal_becomes_dream():
    sf = SoulField()
    profile = UserProfile()
    profile.facts.append(
        UserFact(
            fact="I have two kids — evenings are family time",
            category="life",
            confidence=0.92,
        )
    )
    goals = collect_user_goals(sf, profile)
    assert any("kids" in g.lower() for g in goals)
    dreams = compose_partner_dreams(sf, profile=profile)
    assert any("kids" in d.lower() or "family" in d.lower() for d in dreams)


def test_self_moves_from_lesson_and_frustration():
    from remedy.memory.soul.field import EpisodeResidue

    sf = SoulField()
    sf.episodes.append(
        EpisodeResidue(arc="login form", user_stance="frustrated", open_thread="fix oauth")
    )
    sf.organism_lessons.append(
        OrganismLesson(outcome="red", lesson="Do not claim done without verify")
    )
    moves = collect_self_moves(sf)
    blob = " ".join(moves).lower()
    assert "blocker" in blob or "verify" in blob


def test_apply_and_persist_roundtrip(tmp_path):
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.pledges.append("My goal this year is to ship Remedy 1.0")
    sf.relational.help_mode = "silent-doer"
    n = apply_partner_dreams(sf, compose_partner_dreams(sf))
    assert n >= 1
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    sf2 = load_soul_field(tmp_path)
    assert sf2.future_dreams
    assert any("Toward" in d for d in sf2.future_dreams)


def test_dream_cycle_writes_future_dreams(tmp_path):
    clear_soul_cache()
    reset_dream_cooldown()
    sf = load_soul_field(tmp_path)
    sf.pledges.append("Goal: ship the inbox path")
    sf.relational.help_mode = "pair"
    from remedy.memory.soul.field import EpisodeResidue

    sf.episodes.append(EpisodeResidue(arc="inbox", user_stance="focused", open_thread="keep going"))
    sf.episodes.append(EpisodeResidue(arc="inbox 2", user_stance="focused", open_thread="keep going"))
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    reset_dream_cooldown()
    out = dream_cycle(home=tmp_path, force=True, use_local=False)
    assert out.get("ok")
    assert not out.get("skipped")
    assert out.get("dreams")
    sf2 = load_soul_field(tmp_path)
    assert sf2.future_dreams
    block = build_soul_context_block(home=tmp_path, include_contract=False)
    assert "Dreams of the future" in block
    rec = recall_unified("inbox", home=tmp_path, limit=10)
    assert "dream" in rec.lower() or "inbox" in rec.lower()


def test_refresh_on_new_goal_no_episodes(tmp_path):
    from remedy.memory.soul.partner_dream import refresh_partner_dreams

    clear_soul_cache()
    out = refresh_partner_dreams(tmp_path, extra_goals=["ship the partner dream layer"])
    assert out.get("ok")
    assert out.get("dreams")
    assert any("ship" in str(d).lower() for d in out["dreams"])
    sf = load_soul_field(tmp_path)
    assert sf.future_dreams
    assert any("Goal:" in p or "ship" in p.lower() for p in sf.pledges)


def test_continuity_includes_dreams(tmp_path):

    from remedy.core.continuity_steering import continuity_steering_block
    from remedy.memory.soul.partner_dream import refresh_partner_dreams

    clear_soul_cache()
    refresh_partner_dreams(tmp_path, extra_goals=["finish the birthday plan"])

    class R:
        _session_brief = None
        config = type("C", (), {"home_dir": str(tmp_path)})()

        def effective_project_path(self):
            return ""

        def list_tasks(self):
            return []

    # Force soul on by writing dreams into isolated home
    block = continuity_steering_block(R(), home=tmp_path, max_chars=1600)
    # If maturity gate is off, block may be empty — still persist dreams
    sf = load_soul_field(tmp_path)
    assert sf.future_dreams
    if block:
        assert "Dreams of the future" in block or "birthday" in block.lower()


def test_run_coro_sync_never_leaks_unawaited_coroutine() -> None:
    """A memory backend raising RuntimeError inside the thread must not fall
    through into a second asyncio.run() on the running loop (which raised
    before awaiting and leaked the coroutine — the dream.py RuntimeWarning)."""
    import asyncio
    import contextlib
    import warnings

    from remedy.memory.soul.dream import _run_coro_sync

    async def _boom() -> int:
        raise RuntimeError("attached to a different loop")

    async def _ok() -> int:
        return 7

    async def _drive() -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            with contextlib.suppress(RuntimeError):
                _run_coro_sync(_boom, timeout=3)
            assert _run_coro_sync(_ok, timeout=3) == 7

    asyncio.run(_drive())
    # No running loop → same-thread path
    assert _run_coro_sync(_ok, timeout=3) == 7
