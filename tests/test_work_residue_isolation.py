"""Work residue never crosses sessions or projects through owner-global memory.

On 2026-09-03 a session bound to ``C:\\Users\\Administrator`` was asked
"C:\\Users\\Administrator\\Old-Remedy review". Two hours later a fresh tab in
``claimidx`` said "review" and Remedy searched Old-Remedy. The thread had
travelled through every owner-global store at once:

* ``soul/field.json`` — ``relational.open_threads`` got ``continue: C:\\…\\Old-Remedy
  review``; the dream cycle turned it into a pledge, a "Toward …" dream and a
  self-habit; ``soul_recall`` and the soul inject showed all of them.
* Partner Memory — the dream wrote ``Ongoing focus: continue: C:\\…`` as a
  workflow fact, shown in the Memory panel of every project.
* ``organism.json`` — ``open_hint`` / ``dream`` were copied from those lists.
* ``missions/latest.txt`` — the global pointer handed the old session's active
  mission to the new session's build turn.

The rule these tests enforce: a job (a path, a build goal, a "continue: …"
thread, a tool-retry nudge) is session/project memory. It may live in the
episode residue (stamped with session and project), the session Time Crystal,
the build ledger and the mission store — and readers scope those. It never
enters the relational lists, pledges, habits, dreams, partner facts or organism
vitals, and a store that already holds it is scrubbed on load.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from remedy.core.metabolism.time_crystal import looks_like_work_residue
from remedy.memory.harness.brief import SessionBrief
from remedy.memory.soul.field import (
    clear_soul_cache,
    load_soul_field,
    save_soul_field,
    scrub_work_residue,
)
from remedy.memory.soul.inject import build_soul_context_block, episode_in_scope
from remedy.memory.soul.update import update_soul_after_turn

# Shaped like the real incident, but under a folder that does not exist: the
# continuity block reads the bound project's build ledger from disk, and the
# owner's real ``claimidx`` ledger already carries the leaked paths.
OLD = r"C:\Users\Administrator\NoSuchProjects\Old-Remedy"
CLAIMIDX = r"C:\Users\Administrator\NoSuchProjects\claimidx"
THREAD = f"continue: {OLD} review"


@pytest.fixture(autouse=True)
def _fresh_soul_cache():
    clear_soul_cache()
    yield
    clear_soul_cache()


# --- the predicate ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        THREAD,
        f"Continue: {OLD.lower()} review",
        f"{OLD} review: stay in the build — tools over narration until verified",
        f"Toward {THREAD}: stay in the build",
        "Stay with: Continue remaining work from the last successful tool",
        "Retry or work around the last failing tool(s)",
        "Continue remaining work from the last successful tool",
        f"intent={OLD} review | user: {OLD} review",
        "Ongoing focus: continue: /home/ahmi/proj review",
    ],
)
def test_job_shapes_are_work_residue(text: str) -> None:
    assert looks_like_work_residue(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "check in about the guitar app",
        "Stay in builder mode: tools over narration until green.",
        "When they are frustrated: fix first, explain second.",
        "Toward ship the launch: act first; one short summary after tools",
        "finish soul field organism",
        "Goal: learn guitar by December",
        "remind me tomorrow about the dentist",
        "resume, do not restart; verify before claiming done",
    ],
)
def test_relational_lines_are_not_residue(text: str) -> None:
    assert looks_like_work_residue(text) is False


# --- source: the per-turn soul update -----------------------------------------


def test_brief_intent_stays_out_of_relational_open_threads(tmp_path: Path) -> None:
    brief = SessionBrief(session_id="s-old", intent=f"{OLD} review")
    sf = update_soul_after_turn(
        user_text=f"{OLD} review",
        assistant_text="Scouting the repo.",
        session_id="s-old",
        brief=brief,
        project_path=OLD,
        home=tmp_path,
    )
    assert all(not looks_like_work_residue(t) for t in sf.relational.open_threads)
    assert THREAD not in sf.relational.open_threads
    # The episode still knows what we were doing — stamped so readers can scope it.
    ep = sf.episodes[-1]
    assert ep.open_thread == THREAD
    assert ep.session_id == "s-old"
    assert ep.project_hint.lower().endswith("old-remedy")


def test_brief_open_task_stays_out_of_relational_open_threads(tmp_path: Path) -> None:
    brief = SessionBrief(session_id="s1", open_tasks=["wire OAuth in src/auth.py"])
    sf = update_soul_after_turn(
        user_text="keep going",
        assistant_text="On it.",
        session_id="s1",
        brief=brief,
        home=tmp_path,
    )
    assert "wire OAuth in src/auth.py" not in sf.relational.open_threads
    assert sf.episodes[-1].open_thread == "wire OAuth in src/auth.py"


def test_relational_cue_from_the_person_is_kept(tmp_path: Path) -> None:
    sf = update_soul_after_turn(
        user_text="remind me tomorrow to call the guitar shop",
        assistant_text="Will do.",
        session_id="s2",
        home=tmp_path,
    )
    assert any("guitar shop" in t for t in sf.relational.open_threads)


# --- load-time scrub of an already polluted field ------------------------------


def _polluted_field_json() -> dict[str, Any]:
    """The shape actually found in ~/.remedy/soul/field.json on 2026-09-03."""
    return {
        "schema": 3,
        "identity_name": "Remedy",
        "self_habits": [
            "Stay in builder mode: tools over narration until green.",
            f"{OLD} review: stay in the build — tools over narration until verified",
            f"{OLD} review: resume, do not restart; verify before claiming done",
            "stay in the build — tools over narration until verified",
        ],
        "relational": {
            "rapport": 0.55,
            "trust": 0.55,
            "open_threads": [
                "continue: hi",
                THREAD,
                "Retry or work around the last failing tool(s)",
                "Continue remaining work from the last successful tool",
                "check in about the guitar app",
            ],
            "turns_together": 24,
        },
        "episodes": [
            {
                "id": "808be970e5",
                "ts": 1788441868.8,
                "arc": f"intent={OLD} review | user: {OLD} review",
                "user_stance": "focused",
                "open_thread": THREAD,
                "valence": 0.0,
                "muscle": "xai/grok-4.5",
                "session_id": "08d08ad0",
                "project_hint": "C:/Users/Administrator",
            }
        ],
        "pledges": [f"Stay with: {THREAD}", "Goal: learn guitar by December"],
        "future_dreams": [
            f"Toward {THREAD}: stay in the build — tools over narration until verified",
            "Toward learn guitar by December: act first; one short summary after tools",
        ],
    }


def test_load_scrubs_residue_and_persists_the_clean_field(tmp_path: Path) -> None:
    soul_dir = tmp_path / "soul"
    soul_dir.mkdir()
    (soul_dir / "field.json").write_text(json.dumps(_polluted_field_json()), encoding="utf-8")

    sf = load_soul_field(tmp_path)

    assert sf.relational.open_threads == ["check in about the guitar app"]
    assert sf.pledges == ["Goal: learn guitar by December"]
    assert all(OLD.lower() not in h.lower() for h in sf.self_habits)
    assert "Stay in builder mode: tools over narration until green." in sf.self_habits
    assert sf.future_dreams == [
        "Toward learn guitar by December: act first; one short summary after tools"
    ]
    # Episodes keep their residue (they are scoped by session/project on read).
    assert sf.episodes and sf.episodes[0].open_thread == THREAD
    # And the cleaned field is what is now on disk.
    on_disk = json.loads((soul_dir / "field.json").read_text(encoding="utf-8"))
    assert THREAD not in on_disk["relational"]["open_threads"]
    assert not any(OLD in p for p in on_disk["pledges"])


def test_scrub_is_idempotent_and_counts() -> None:
    from remedy.memory.soul.field import SoulField

    raw = _polluted_field_json()
    sf = SoulField()
    sf.relational.open_threads = list(raw["relational"]["open_threads"])
    sf.pledges = list(raw["pledges"])
    sf.self_habits = list(raw["self_habits"])
    sf.future_dreams = list(raw["future_dreams"])
    removed = scrub_work_residue(sf)
    assert removed == 4 + 1 + 2 + 1  # threads, pledge, habits, dream
    assert scrub_work_residue(sf) == 0


def test_save_enforces_the_rule_on_every_writer(tmp_path: Path) -> None:
    sf = load_soul_field(tmp_path)
    sf.relational.open_threads.append(THREAD)
    sf.pledges.append(f"Stay with: {THREAD}")
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    again = load_soul_field(tmp_path)
    assert THREAD not in again.relational.open_threads
    assert not any(OLD in p for p in again.pledges)


# --- readers: inject, recall, steering, organism --------------------------------


def _field_with_episodes(tmp_path: Path):
    """Two sessions, two projects, in one owner-global field."""
    update_soul_after_turn(
        user_text=f"{OLD} review",
        assistant_text="Reading Old-Remedy.",
        session_id="s-old",
        brief=SessionBrief(session_id="s-old", intent=f"{OLD} review"),
        project_path=OLD,
        home=tmp_path,
    )
    return update_soul_after_turn(
        user_text="review",
        assistant_text="Reading claimidx.",
        session_id="s-claim",
        brief=SessionBrief(session_id="s-claim", intent="review claimidx"),
        project_path=CLAIMIDX,
        home=tmp_path,
    )


def test_inject_scopes_episodes_to_the_current_project(tmp_path: Path) -> None:
    _field_with_episodes(tmp_path)
    block = build_soul_context_block(
        home=tmp_path, project_path=CLAIMIDX, session_id="s-claim", work_threads=True
    )
    assert "old-remedy" not in block.lower()
    assert "claimidx" in block.lower()


def test_inject_with_no_project_shows_only_own_session(tmp_path: Path) -> None:
    _field_with_episodes(tmp_path)
    block = build_soul_context_block(home=tmp_path, project_path="", session_id="s-new")
    assert "old-remedy" not in block.lower()
    assert "claimidx" not in block.lower()


def test_episode_scope_rules() -> None:
    ep_old = SimpleNamespace(session_id="s-old", project_hint=OLD.replace("\\", "/"))
    ep_legacy = SimpleNamespace(session_id="", project_hint="")
    assert episode_in_scope(ep_old, project_path=OLD, session_id="s-x") is True
    assert episode_in_scope(ep_old, project_path=CLAIMIDX, session_id="s-x") is False
    assert episode_in_scope(ep_old, project_path="", session_id="s-old") is True
    assert episode_in_scope(ep_old, project_path="", session_id="s-x") is False
    assert episode_in_scope(ep_legacy, project_path=CLAIMIDX, session_id="s-x") is True


def test_recall_does_not_surface_another_projects_job(tmp_path: Path) -> None:
    from remedy.memory.soul.recall import recall_unified

    _field_with_episodes(tmp_path)
    sf = load_soul_field(tmp_path)
    # Pretend a pre-fix writer left residue in the global lists anyway.
    sf.relational.open_threads.append(THREAD)
    sf.self_habits.append(f"{OLD} review: stay in the build")
    text = recall_unified(
        "review project", home=tmp_path, session_id="s-claim", project_path=CLAIMIDX
    )
    assert "old-remedy" not in text.lower()


def test_continuity_steering_ignores_residue_threads(tmp_path: Path) -> None:
    from remedy.core.continuity_steering import continuity_steering_block

    sf = load_soul_field(tmp_path)
    sf.relational.open_threads = [THREAD, "check in about the guitar app"]
    sf.future_dreams = [f"Toward {THREAD}: stay in the build"]

    class R:
        _session_brief = SessionBrief(session_id="s-claim", intent="review claimidx")

        def effective_project_path(self):
            return CLAIMIDX

    block = continuity_steering_block(R(), home=tmp_path, max_chars=1500)
    assert "old-remedy" not in block.lower()
    assert "guitar app" in block.lower()


def test_organism_hint_and_dream_skip_residue() -> None:
    from remedy.core.metabolism.organism import _first_clean_dream, _last_relational_thread

    threads = ["check in about the guitar app", THREAD, "Retry or work around the last failing tool(s)"]
    assert _last_relational_thread(threads) == "check in about the guitar app"
    assert _last_relational_thread([THREAD]) == ""
    dreams = [f"Toward {THREAD}: stay in the build", "Toward learn guitar: act first"]
    assert _first_clean_dream(dreams) == "Toward learn guitar: act first"


# --- dream cycle: no promotion, no partner fact ---------------------------------


class _FakeMemory:
    def __init__(self, profile: Any) -> None:
        self.profile = profile
        self.saved = 0

    async def get_or_create_profile(self) -> Any:
        return self.profile

    async def save_user_profile(self, profile: Any) -> None:
        self.saved += 1


def test_dream_cycle_never_promotes_or_publishes_the_job(tmp_path: Path) -> None:
    from remedy.memory.profile import UserFact, UserProfile
    from remedy.memory.soul.dream import dream_cycle, reset_dream_cooldown

    reset_dream_cooldown()
    brief = SessionBrief(session_id="s-old", intent=f"{OLD} review")
    for i in range(4):
        update_soul_after_turn(
            user_text=f"{OLD} review" if i == 0 else "keep going",
            assistant_text="Working.",
            session_id="s-old",
            brief=brief,
            project_path=OLD,
            home=tmp_path,
        )
    profile = UserProfile(user_id="owner")
    profile.facts.append(
        UserFact(fact=f"Ongoing focus: {THREAD}", category="workflow", source="soul_dream")
    )
    profile.facts.append(UserFact(fact="Plays guitar on weekends", category="life", source="explicit"))
    mem = _FakeMemory(profile)

    dream_cycle(home=tmp_path, force=True, memory=mem, use_local=False, session_id="s-old")

    sf = load_soul_field(tmp_path)
    assert all(not looks_like_work_residue(t) for t in sf.relational.open_threads)
    assert all(not looks_like_work_residue(p) for p in sf.pledges)
    assert all(OLD.lower() not in h.lower() for h in sf.self_habits)
    assert all(OLD.lower() not in d.lower() for d in sf.future_dreams)
    # The stale "Ongoing focus" fact was purged; the owner's own fact survived.
    texts = [f.fact for f in profile.facts]
    assert not any("Ongoing focus" in t for t in texts)
    assert "Plays guitar on weekends" in texts


# --- partner memory ------------------------------------------------------------


def test_partner_fact_residue_rules() -> None:
    from remedy.memory.partner_memory import (
        fact_is_work_residue,
        purge_work_residue_facts,
        rank_injectable_facts,
    )
    from remedy.memory.profile import UserFact, UserProfile

    inferred = UserFact(fact=f"Ongoing focus: {THREAD}", category="workflow", source="soul_dream")
    owner_path = UserFact(
        fact=r"My main repo lives at C:\dev\remedy", category="work", source="explicit",
        authority="owner", inferred=False, confidence=0.95,
    )
    life = UserFact(fact="Has two kids", category="life", source="explicit", confidence=0.9)
    assert fact_is_work_residue(inferred) is True
    assert fact_is_work_residue(owner_path) is False
    assert fact_is_work_residue(life) is False

    profile = UserProfile(user_id="owner")
    profile.facts.extend([inferred, owner_path, life])
    ranked = [f.fact for f in rank_injectable_facts(profile, query="review project")]
    assert inferred.fact not in ranked
    assert owner_path.fact in ranked
    assert purge_work_residue_facts(profile) == 1
    assert len(profile.facts) == 2


# --- missions: the global pointer is not a session's mission ---------------------


def test_mission_latest_is_session_only(tmp_path: Path) -> None:
    from remedy.core.mission import MissionStore, create_mission

    old = create_mission(f"{OLD} review", session_id="s-old", home=tmp_path)
    store = MissionStore(tmp_path)
    # The global pointer exists (anonymous CLI callers still use it) …
    assert (tmp_path / "missions" / "latest.txt").read_text(encoding="utf-8").strip() == old.id
    assert store.latest() is not None
    # … but a session with no mission of its own gets nothing, not the other tab's.
    assert store.latest("s-claim") is None
    # An id the sanitizer rejects is treated as anonymous (CLI / legacy callers)
    # and keeps the global pointer — only real session ids are isolated.
    assert store.latest("../evil") is not None
    mine = create_mission("review claimidx", session_id="s-claim", home=tmp_path)
    assert store.latest("s-claim").id == mine.id
    assert store.latest("s-old").id == old.id


def test_mission_pointer_naming_another_sessions_mission_is_ignored(tmp_path: Path) -> None:
    from remedy.core.mission import MissionStore, create_mission

    old = create_mission(f"{OLD} review", session_id="s-old", home=tmp_path)
    (tmp_path / "missions" / "latest-s-claim.txt").write_text(old.id, encoding="utf-8")
    assert MissionStore(tmp_path).latest("s-claim") is None


def test_build_turn_does_not_adopt_another_sessions_mission(tmp_path: Path) -> None:
    from remedy.core.build_mission import ensure_build_mission
    from remedy.core.mission import MissionStore, create_mission

    old = create_mission(
        f"{OLD} review", session_id="s-old", verify_command="pytest -q", home=tmp_path,
        project_path=OLD,
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(home_dir=str(tmp_path)),
        effective_project_path=lambda: CLAIMIDX,
    )
    state = SimpleNamespace(goal="review claimidx", verify_command="", project_path=CLAIMIDX, mission_id="")
    res = ensure_build_mission(runtime, state, session_id="s-claim")
    assert res["created"] is True
    assert res["mission_id"] != old.id
    m = MissionStore(tmp_path).get(res["mission_id"])
    assert m is not None and m.goal == "review claimidx"
    assert m.project_path == CLAIMIDX
    assert m.verify_command is None  # never inherit the other project's oracle


def test_build_turn_drops_own_mission_from_another_project(tmp_path: Path) -> None:
    from remedy.core.build_mission import ensure_build_mission
    from remedy.core.mission import create_mission

    stale = create_mission(
        f"{OLD} review", session_id="s1", home=tmp_path, project_path=OLD
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(home_dir=str(tmp_path)),
        effective_project_path=lambda: CLAIMIDX,
    )
    state = SimpleNamespace(goal="review claimidx", verify_command="", project_path=CLAIMIDX, mission_id="")
    res = ensure_build_mission(runtime, state, session_id="s1")
    assert res["created"] is True and res["mission_id"] != stale.id


def test_soul_missions_never_arm_a_job_thread(tmp_path: Path) -> None:
    from remedy.memory.soul.missions_bridge import collect_soul_mission_candidates

    sf = load_soul_field(tmp_path)
    sf.relational.open_threads = [THREAD, "check in about the guitar app tomorrow"]
    sf.pledges = [f"Stay with: {THREAD}"]
    goals = [c["goal"] for c in collect_soul_mission_candidates(tmp_path)]
    assert not any(OLD in g for g in goals)
