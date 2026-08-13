"""Living organism memory — life, goals, work, this-turn recall."""

from __future__ import annotations

from remedy.memory.living import (
    category_boost,
    extract_living_facts,
    format_living_sections,
    format_turn_recall,
    turn_kind,
)
from remedy.memory.partner_memory import (
    build_partner_memory_block,
    extract_heuristic_facts,
    rank_injectable_facts,
)
from remedy.memory.profile import UserFact, UserProfile


def test_turn_kind_life_vs_code():
    assert turn_kind("can you help me plan my kid's birthday") == "life"
    assert turn_kind("fix the pytest failure in test_rmb") == "code"
    assert turn_kind("make the UI less rounded, keep it brutalist") == "design"
    assert turn_kind("my goal this month is to ship 1.0") == "goal"


def test_extract_life_and_goal():
    facts = extract_living_facts(
        "I have two kids. My goal this year is to ship Remedy 1.0. Be blunt."
    )
    cats = {f.category for f in facts}
    texts = " ".join(f.text.lower() for f in facts)
    assert "life" in cats
    assert "goal" in cats
    assert "kids" in texts
    assert "ship" in texts or "remedy" in texts
    assert any(f.category == "preference" for f in facts) or "blunt" in texts


def test_extract_correction_and_design():
    facts = extract_living_facts(
        "Too generic, too AI-looking. No rounded corners. Keep it brutalist."
    )
    cats = {f.category for f in facts}
    assert "correction" in cats or "design" in cats
    assert any("rounded" in f.text.lower() or "brutalist" in f.text.lower() for f in facts)


def test_ephemeral_mood_not_stored():
    assert extract_living_facts("I'm tired today") == []
    assert extract_living_facts("right now I have a headache") == []


def test_heuristic_merge_includes_living():
    facts = extract_heuristic_facts(
        "I have two kids. Don't mention my divorce. I prefer TypeScript."
    )
    texts = " ".join(f.text.lower() for f in facts)
    assert "typescript" in texts
    assert "kids" in texts or "divorce" in texts


def test_life_turn_ranks_life_above_craft():
    profile = UserProfile()
    profile.facts.append(
        UserFact(fact="Use ruff and pytest on this repo", category="craft", confidence=0.92)
    )
    profile.facts.append(
        UserFact(fact="I have two kids — evenings are family time", category="life", confidence=0.9)
    )
    ranked = rank_injectable_facts(
        profile, query="can you keep tonight light, family stuff", min_confidence=0.5
    )
    assert ranked
    assert ranked[0].category == "life"


def test_sectioned_inject_has_life_and_work():
    profile = UserProfile(display_name="Ahmi")
    profile.facts.append(
        UserFact(fact="I have two kids", category="life", confidence=0.95, source="living")
    )
    profile.facts.append(
        UserFact(fact="I prefer TypeScript", category="preference", confidence=0.9)
    )
    profile.facts.append(
        UserFact(
            fact="Use pnpm in this monorepo",
            category="stack",
            confidence=0.9,
            project_path=r"C:\work\RemedyAI",
        )
    )
    block = build_partner_memory_block(profile, project_path=r"C:\work\RemedyAI")
    assert "Partner memory" in block
    assert "Ahmi" in block
    assert "Life & goals" in block
    assert "How we work together" in block
    assert "This chapter" in block
    assert "two kids" in block
    assert "TypeScript" in block
    assert "pnpm" in block


def test_format_turn_recall_skips_already():
    hits = [
        {"kind": "fact", "content": "I have two kids"},
        {"kind": "entry", "content": "Last week we planned the birthday party"},
    ]
    lines = format_turn_recall(hits, already={"I have two kids"})
    assert lines
    assert any("birthday" in ln.lower() for ln in lines)
    assert not any("two kids" in ln.lower() for ln in lines)


def test_category_boost_life():
    assert category_boost("life", "life") > category_boost("craft", "life")
    assert category_boost("craft", "code") > category_boost("life", "code")


def test_whoami_sections_life_and_work():
    from remedy.memory.partner_memory import format_whoami

    profile = UserProfile(display_name="Sam")
    profile.facts.append(
        UserFact(
            fact="I have two kids",
            category="life",
            confidence=0.95,
            source="living",
        )
    )
    profile.facts.append(
        UserFact(
            fact="I prefer TypeScript",
            category="preference",
            confidence=0.9,
            source="heuristic",
        )
    )
    text = format_whoami(profile)
    assert "Sam" in text
    assert "TypeScript" in text
    assert "two kids" in text
    assert "Life & goals" in text
    assert "How we work together" in text
    assert "/forget" in text


def test_life_goal_lines_filters():
    from remedy.memory.living import life_goal_lines

    profile = UserProfile()
    profile.facts.append(UserFact(fact="I have two kids", category="life", confidence=0.9))
    profile.facts.append(UserFact(fact="I prefer TypeScript", category="preference", confidence=0.9))
    profile.facts.append(UserFact(fact="maybe", category="life", confidence=0.2))
    lines = life_goal_lines(profile)
    assert any("kids" in x.lower() for x in lines)
    assert not any("TypeScript" in x for x in lines)


def test_continuity_includes_life_when_profile_present():
    from pathlib import Path

    from remedy.core.continuity_steering import continuity_steering_block
    from remedy.memory.harness.brief import SessionBrief

    profile = UserProfile()
    profile.facts.append(
        UserFact(fact="I have two kids — evenings are family time", category="life", confidence=0.92)
    )

    class R:
        _session_brief = SessionBrief(session_id="s")
        _user_profile = profile
        config = type("C", (), {"home_dir": str(Path("C:/no/soul"))})()

        def effective_project_path(self):
            return ""

        def list_tasks(self):
            return []

    block = continuity_steering_block(R(), home=Path("C:/no/soul"), max_chars=1200)
    assert "Continuity" in block
    assert "kids" in block.lower()
    assert "Life & goals" in block


def test_soul_update_promotes_living_goal(tmp_path):
    from remedy.memory.soul.field import load_soul_field
    from remedy.memory.soul.update import update_soul_after_turn

    home = tmp_path / "soul"
    home.mkdir()
    sf = update_soul_after_turn(
        user_text="My goal this year is to ship Remedy 1.0",
        assistant_text="I'll keep that as a life goal.",
        session_id="s",
        home=home,
    )
    assert any("ship" in p.lower() or "remedy" in p.lower() for p in sf.pledges)
    saved = load_soul_field(home)
    assert saved.pledges


def test_project_chapter_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.core.project_learning import (
        clear_project_profile_cache,
        project_chapter_block,
        record_project_chapter,
    )

    clear_project_profile_cache()
    root = tmp_path / "app"
    root.mkdir()
    record_project_chapter(str(root), decision="Ship the inbox path, no extra branches")
    block = project_chapter_block(str(root), query="inbox submit")
    assert "This chapter" in block
    assert "inbox" in block.lower()


def test_living_sections_budget():
    buckets = {
        "who": ["- (identity) my name is Sam"],
        "life": [f"- (life) durable life fact number {i} about family" for i in range(20)],
        "work": [f"- (preference) durable work manner {i} stay concise" for i in range(20)],
        "chapter": [],
    }
    out = format_living_sections(buckets, name="Sam", max_chars=600)
    assert len(out) <= 700
    assert "Partner memory" in out
    assert "Sam" in out
