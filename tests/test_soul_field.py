"""Soul Field — provider-invariant personhood layer."""

from __future__ import annotations

from remedy.memory.soul.field import (
    MAX_EPISODES,
    MAX_PLEDGES,
    MAX_SELF_LESSONS,
    EpisodeResidue,
    OrganismLesson,
    SoulField,
    clear_soul_cache,
    encode_lesson_strength,
    encode_strength,
    episode_retention,
    load_soul_field,
    pledge_trace_touch,
    retain_episodes,
    retain_lessons,
    save_soul_field,
)
from remedy.memory.soul.inject import build_soul_context_block, provider_muscle_contract
from remedy.memory.soul.update import record_self_inject_lesson, update_soul_after_turn


def test_looks_like_secret_soul_catches_password_prose():
    from remedy.memory.soul.field import looks_like_secret_soul

    assert looks_like_secret_soul("my password is hunter2")
    assert looks_like_secret_soul("token: abcdef")
    assert not looks_like_secret_soul("we walked through the garden")


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


# --- salience-weighted retention + reconsolidation -------------------------


def test_encode_strength_scales_with_intensity():
    # A calm turn encodes weakly; an intense one (either sign) sticks harder.
    calm = encode_strength(0.0)
    delight = encode_strength(0.9)
    frustration = encode_strength(-0.9)
    assert delight > calm
    assert frustration > calm
    assert 0.05 <= calm <= 1.0 and delight <= 1.0


def test_retention_keeps_salient_old_over_trivial_recent():
    # 20 episodes, one *old* but high-strength/recently-recalled; the rest are
    # trivial. FIFO would drop the old one; salience must keep it.
    day = 86400.0
    now = 100 * day
    eps: list[EpisodeResidue] = []
    for i in range(20):
        eps.append(
            EpisodeResidue(
                id=f"e{i}",
                ts=(i + 1) * day,          # oldest first
                arc=f"trivial turn {i}",
                strength=0.05,
                last_recall_ts=(i + 1) * day,
            )
        )
    # Make an early episode pivotal and kept warm by recent recall.
    eps[2].arc = "the pivotal decision"
    eps[2].strength = 1.0
    eps[2].recalls = 5
    eps[2].last_recall_ts = now
    kept = retain_episodes(eps, now, cap=MAX_EPISODES)
    assert len(kept) == MAX_EPISODES
    ids = {e.id for e in kept}
    assert "e2" in ids, "salient old episode must survive eviction"
    # The 4 most-recent are always protected.
    for i in range(16, 20):
        assert f"e{i}" in ids
    # Chronological order preserved for tail-slicing injectors.
    assert [e.ts for e in kept] == sorted(e.ts for e in kept)


def test_retention_protects_last_appended_despite_backward_clock():
    # A backward clock step: the newest (last-appended) episode carries a SMALLER
    # ts than older ones. Protection is by list position, so it must survive —
    # never be evicted the same instant it's stored.
    eps = [
        EpisodeResidue(id=f"e{i}", ts=1000.0 + i, arc=f"t{i}", strength=0.1)
        for i in range(15)
    ]
    eps[-1].ts = 1.0  # clock jumped backward for the freshest episode
    eps[-1].id = "just_now"
    kept = retain_episodes(eps, now=2000.0, cap=MAX_EPISODES)
    assert "just_now" in {e.id for e in kept}


def test_retention_noop_under_cap():
    eps = [EpisodeResidue(id=f"e{i}", ts=float(i), arc=f"t{i}") for i in range(5)]
    kept = retain_episodes(eps, now=1000.0, cap=MAX_EPISODES)
    assert len(kept) == 5


def test_episode_retention_decays_with_age():
    now = 1_000_000.0
    fresh = EpisodeResidue(arc="x", strength=0.8, last_recall_ts=now)
    stale = EpisodeResidue(arc="x", strength=0.8, last_recall_ts=now - 200 * 86400.0)
    assert episode_retention(fresh, now) > episode_retention(stale, now)


def test_reconsolidation_strengthens_recalled_episode(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="Let's design the kubernetes autoscaler ingress controller.",
        assistant_text="Sketched the autoscaler and ingress wiring.",
        session_id="s-recon",
        home=tmp_path,
    )
    # A few unrelated turns.
    for i in range(3):
        update_soul_after_turn(
            user_text=f"unrelated chatter number {i} about lunch plans",
            assistant_text="noted",
            session_id="s-recon",
            home=tmp_path,
        )
    # Now the topic comes back up — the original episode should reconsolidate.
    sf = update_soul_after_turn(
        user_text="how is the kubernetes autoscaler coming along?",
        assistant_text="Progressing.",
        session_id="s-recon",
        home=tmp_path,
    )
    recalled = [e for e in sf.episodes if "kubernetes autoscaler" in e.arc.lower()]
    assert recalled, "the kubernetes episode should still be present"
    assert any(e.recalls >= 1 for e in recalled), "recall should reconsolidate it"


def test_lesson_retention_keeps_old_red_over_recent_greens():
    # 30 lessons: one old hard-won failure among routine greens. FIFO would
    # drop it; the trace spine must keep it.
    day = 86400.0
    now = 100 * day
    lessons = [
        OrganismLesson(
            ts=(i + 1) * day,
            outcome="green",
            lesson=f"routine green {i}",
            strength=encode_lesson_strength("green"),
            last_recall_ts=(i + 1) * day,
        )
        for i in range(30)
    ]
    lessons[1].outcome = "red"
    lessons[1].lesson = "never force-apply on red"
    lessons[1].strength = encode_lesson_strength("red")
    lessons[1].recalls = 3
    lessons[1].last_recall_ts = now
    kept = retain_lessons(lessons, now, cap=MAX_SELF_LESSONS)
    assert len(kept) == MAX_SELF_LESSONS
    assert any("force-apply" in x.lesson for x in kept), (
        "hard-won old red lesson must survive eviction"
    )
    # The last-appended few are protected regardless.
    assert any("routine green 29" in x.lesson for x in kept)


def test_lesson_strength_encoded_by_outcome(tmp_path):
    clear_soul_cache()
    sf = record_self_inject_lesson(outcome="red", tree="python", home=tmp_path)
    assert sf.organism_lessons[-1].strength >= 0.8
    sf = record_self_inject_lesson(outcome="green", tree="python", home=tmp_path)
    assert 0.5 <= sf.organism_lessons[-1].strength < 0.8


def test_pledge_restatement_reconsolidates(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="From now on we always run the tests before shipping.",
        assistant_text="Understood.",
        home=tmp_path,
    )
    sf = update_soul_after_turn(
        user_text="Remember: from now on we always run the tests before shipping.",
        assistant_text="Always.",
        home=tmp_path,
    )
    assert sf.pledges, "the pledge should be held"
    # Re-statement must have reconsolidated at least one trace.
    assert any(
        int(tr.get("recalls") or 0) >= 1 for tr in sf.pledge_traces.values()
    ), "re-stating a pledge should count as a recall"


def test_new_pledge_lands_when_list_full():
    # The old `[:12]` cap kept the FIRST 12 — a new pledge could never land.
    sf = SoulField()
    now = 1_000_000.0
    for i in range(MAX_PLEDGES):
        p = f"old pledge number {i}"
        sf.pledges.append(p)
        pledge_trace_touch(sf, p, now)
    sf.pledges.append("the brand new commitment")
    pledge_trace_touch(sf, "the brand new commitment", now)
    sf.touch()
    assert len(sf.pledges) <= MAX_PLEDGES
    assert "the brand new commitment" in sf.pledges, (
        "a freshly stated pledge must land even when the list is full"
    )


def test_pledge_traces_roundtrip_and_gc(tmp_path):
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.pledges.append("keep work local until tested")
    pledge_trace_touch(sf, "keep work local until tested")
    sf.pledge_traces["a pledge that was dropped"] = {"strength": 0.9}
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    sf2 = load_soul_field(tmp_path)
    assert "keep work local until tested" in sf2.pledge_traces
    # Trace for a pledge no longer on the list is garbage-collected.
    assert "a pledge that was dropped" not in sf2.pledge_traces


def test_old_episode_backfills_strength_on_load():
    # An episode saved before this layer existed (no strength field) should
    # enter the curve with a sensible non-zero trace, not 0.
    raw = {
        "episodes": [
            {"id": "old1", "ts": 123.0, "arc": "an emotional win", "valence": 0.9}
        ]
    }
    sf = SoulField.from_dict(raw)
    assert sf.episodes
    assert sf.episodes[0].strength > 0.0
