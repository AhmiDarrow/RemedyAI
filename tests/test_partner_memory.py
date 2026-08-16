"""Partner Memory: inject budget, heuristic distill, forget, secret guard."""

from __future__ import annotations

import asyncio

import pytest

from remedy.memory.partner_memory import (
    DEFAULT_MAX_CHARS,
    MAX_HOT_FACTS,
    MAX_HOT_TRAITS,
    MIN_INJECT_CONFIDENCE,
    build_partner_memory_block,
    distill_user_text,
    extract_heuristic_facts,
    forget_facts,
    format_whoami,
    looks_like_secret,
    rank_injectable_facts,
    upsert_profile_fact,
)
from remedy.memory.profile import UserFact, UserProfile
from remedy.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "partner_memory.db"
    s = MemoryStore(db)
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


def test_extract_prefer_and_never():
    facts = extract_heuristic_facts(
        "I prefer TypeScript for frontend. Please never force-push main."
    )
    texts = " ".join(f.text.lower() for f in facts)
    assert "typescript" in texts or "prefer" in texts
    assert any(f.confidence >= 0.85 for f in facts)


def test_noisy_always_run_tests_not_stored():
    facts = extract_heuristic_facts("always run the tests now")
    assert facts == []


def test_extract_name():
    facts = extract_heuristic_facts("Hi, my name is Alex.")
    assert facts
    assert any(f.category == "identity" for f in facts)


def test_secret_guard():
    assert looks_like_secret("api_key=sk-abcdefghijklmnopqrstuv")
    assert looks_like_secret("Bearer abcdefghijklmnop")
    assert not looks_like_secret("I prefer dark mode")
    assert extract_heuristic_facts("remember that password: hunter2 api_key=sk-abc1234567890") == []


def test_force_cannot_bypass_secret_guard():
    """force=True relaxes stability only — never stores credential-shaped text."""
    profile = UserProfile()
    fact, action = upsert_profile_fact(
        profile,
        "api_key=sk-abcdefghijklmnopqrstuvwxyz0123",
        category="general",
        confidence=0.99,
        source="explicit",
        force=True,
    )
    assert fact is None
    assert action == "skipped"
    assert profile.facts == []


def test_inject_budget_cap():
    profile = UserProfile()
    for i in range(40):
        profile.facts.append(
            UserFact(
                fact=f"Stable preference number {i}: always use tool chain variant {i}",
                category="preference",
                confidence=0.9,
            )
        )
    block = build_partner_memory_block(profile, max_chars=DEFAULT_MAX_CHARS)
    assert block
    assert len(block) <= DEFAULT_MAX_CHARS + 80  # header + address line slack
    assert "Partner memory" in block


def test_hot_block_fact_and_trait_caps():
    """Hot inject ranks at most MAX_HOT_FACTS / MAX_HOT_TRAITS items."""
    from remedy.memory.profile import UserTrait

    assert MAX_HOT_FACTS == 14
    assert MAX_HOT_TRAITS == 10
    profile = UserProfile()
    for i in range(30):
        profile.facts.append(
            UserFact(
                fact=f"Durable fact {i:02d} about preferred toolchain option {i}",
                category="preference",
                confidence=0.95,
            )
        )
    profile.traits = {
        f"trait_{i}": UserTrait(key=f"trait_{i}", value=f"v{i}", confidence=0.9)
        for i in range(30)
    }
    # Generous char budget so item caps (not max_chars) bind first
    block = build_partner_memory_block(profile, max_chars=50_000)
    assert block
    body = [ln for ln in block.splitlines() if ln.startswith("- ")]
    trait_lines = [ln for ln in body if any(f"trait_{i}:" in ln for i in range(30))]
    fact_lines = [ln for ln in body if "(preference)" in ln]
    assert len(trait_lines) <= MAX_HOT_TRAITS
    assert len(fact_lines) <= MAX_HOT_FACTS


def test_low_confidence_not_injected():
    profile = UserProfile()
    profile.facts.append(
        UserFact(fact="Maybe likes rust sometimes", category="general", confidence=0.3)
    )
    profile.facts.append(
        UserFact(fact="I prefer TypeScript", category="preference", confidence=0.9)
    )
    block = build_partner_memory_block(profile)
    assert "TypeScript" in block
    assert "Maybe likes rust" not in block


def test_forget_removes_fact():
    profile = UserProfile()
    profile.facts.append(
        UserFact(fact="Deploy host is prod.example.com", category="general", confidence=0.95)
    )
    profile.facts.append(
        UserFact(fact="I prefer TypeScript", category="preference", confidence=0.9)
    )
    removed = forget_facts(profile, "deploy host")
    assert len(removed) == 1
    assert "TypeScript" in profile.facts[0].fact
    block = build_partner_memory_block(profile)
    assert "Deploy host" not in block
    assert "TypeScript" in block


def test_upsert_dedup_reinforces():
    profile = UserProfile()
    f1, a1 = upsert_profile_fact(
        profile, "I prefer TypeScript", category="preference", confidence=0.9
    )
    f2, a2 = upsert_profile_fact(
        profile, "I prefer TypeScript", category="preference", confidence=0.95
    )
    assert a1 == "added"
    assert a2 == "reinforced"
    assert len(profile.facts) == 1
    assert f2 is not None and f2.reference_count >= 2


@pytest.mark.asyncio
async def test_distill_persists_across_reload(store):
    r = await distill_user_text(
        store, "I prefer using git reflog for recovery. Never force-push main."
    )
    assert r["added"] >= 1
    # Reload from disk
    store2 = MemoryStore(store.path)
    await store2.initialize()
    try:
        p2 = await store2.get_or_create_profile()
        block = build_partner_memory_block(p2)
        assert block
        # At least one high-conf preference should inject
        injectable = rank_injectable_facts(p2, min_confidence=MIN_INJECT_CONFIDENCE)
        assert injectable
    finally:
        await store2.close()


@pytest.mark.asyncio
async def test_forget_via_profile_roundtrip(store):
    await distill_user_text(store, "I prefer TypeScript for all UI work.")
    profile = await store.get_or_create_profile()
    assert profile.facts
    forget_facts(profile, "TypeScript")
    await store.save_user_profile(profile)
    p2 = await store.get_or_create_profile()
    assert not any("TypeScript" in f.fact for f in p2.facts)


def test_whoami_friendly():
    profile = UserProfile(display_name="Sam")
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
    assert "/forget" in text
    assert "How we work together" in text or "Facts" in text


def test_project_scope_and_pin():
    from remedy.memory.partner_memory import pin_facts, rank_injectable_facts

    profile = UserProfile()
    profile.facts.append(
        UserFact(
            fact="Use pnpm in this monorepo",
            category="preference",
            confidence=0.9,
            project_path=r"C:\work\RemedyAI",
        )
    )
    profile.facts.append(
        UserFact(
            fact="Other project uses yarn",
            category="preference",
            confidence=0.9,
            project_path=r"C:\work\Other",
        )
    )
    profile.facts.append(
        UserFact(fact="I prefer dark mode", category="preference", confidence=0.9)
    )
    ranked = rank_injectable_facts(
        profile, project_path=r"C:\work\RemedyAI", min_confidence=0.5
    )
    texts = [f.fact for f in ranked]
    assert any("pnpm" in t for t in texts)
    assert any("dark mode" in t for t in texts)
    assert not any("yarn" in t for t in texts)

    pin_facts(profile, "dark mode", pinned=True)
    assert any(f.pinned for f in profile.facts if "dark" in f.fact.lower())


def test_token_overlap_and_hybrid_search_order():
    from remedy.memory.partner_memory import token_overlap_score

    assert token_overlap_score("git reflog recovery", "prefers git reflog") > 0
    assert token_overlap_score("typescript ui", "deploy kubernetes host") < token_overlap_score(
        "typescript ui", "I prefer TypeScript for UI"
    )


@pytest.mark.asyncio
async def test_hybrid_search_includes_facts(store):
    from remedy.memory.partner_memory import search_partner_and_entries

    await distill_user_text(store, "I prefer using git reflog for recovery.")
    hits = await search_partner_and_entries(store, "git recovery", limit=8)
    assert hits
    assert any(h.get("kind") == "fact" for h in hits)


def test_autonomous_policy_pack():
    from remedy.core.intent_policy import policy_for_intent
    from remedy.nanoswarm.router_nanobot import RouterNanobot

    pack = policy_for_intent(
        "chat",
        user_text="Handle this on your own — I need to go be with my kids.",
    )
    assert pack["id"] == "autonomous"
    assert "work alone" in pack["system"].lower() or "agency" in pack["system"].lower()

    r = RouterNanobot().classify_intent(
        "Please work alone and finish without me: implement the memory plan."
    )
    assert r["label"] == "autonomous"


def test_skill_rank_prefers_lower_cost():
    from remedy.models import Skill, SkillManifest, SkillStatus
    from remedy.skills.registry import SkillRegistry

    reg = SkillRegistry()
    slow = Skill(
        manifest=SkillManifest(
            name="slow-skill",
            description="recover git commits",
            status=SkillStatus.ACTIVE,
            metadata={"avg_duration_ms": 120_000.0, "success_rate": 0.9},
        ),
        instructions="x",
    )
    fast = Skill(
        manifest=SkillManifest(
            name="fast-skill",
            description="recover git commits",
            status=SkillStatus.ACTIVE,
            metadata={"avg_duration_ms": 800.0, "success_rate": 0.9},
        ),
        instructions="x",
    )
    for s in (slow, fast):
        reg._skills[s.id] = s  # noqa: SLF001
        reg._by_name[s.manifest.name] = s.id  # noqa: SLF001
    ranked = reg.match_skills("recover git commits", limit=5)
    names = [s.manifest.name for s, _ in ranked]
    assert names[0] == "fast-skill"
