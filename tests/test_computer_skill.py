"""Computer skill memory — she learns how each site wants to be driven."""

from __future__ import annotations

from remedy.core.computer.computer_skill import (
    MIN_EVIDENCE,
    approach_of,
    preferred_click_approach,
    record_action,
    steer_hint,
)

# --- approach classification ----------------------------------------------


def test_approach_of_click():
    assert approach_of("click", {"text": "Sign in"}) == "text"
    assert approach_of("click", {"ref": "e3"}) == "ref"
    assert approach_of("click", {"x": 100, "y": 200}) == "coords"
    assert approach_of("click", {}) == "unknown"


def test_approach_of_other_actions():
    assert approach_of("act", {}) == "act"
    assert approach_of("type", {}) == "type"
    assert approach_of("key", {}) == "key"


# --- recording + preference ------------------------------------------------


def test_records_and_prefers_the_winner(tmp_path):
    for _ in range(4):
        record_action("shop.example.com", "click", "text", True, home=tmp_path)
    for _ in range(4):
        record_action("shop.example.com", "click", "coords", False, home=tmp_path)
    assert preferred_click_approach("shop.example.com", tmp_path) == "text"


def test_thin_evidence_does_not_prefer(tmp_path):
    record_action("x.com", "click", "text", True, home=tmp_path)  # 1 < MIN_EVIDENCE
    assert preferred_click_approach("x.com", tmp_path) is None


def test_higher_rate_wins_over_more_volume(tmp_path):
    # ref: 3/3 perfect; text: 5/10 shaky → prefer the reliable one.
    for _ in range(3):
        record_action("app.example.com", "click", "ref", True, home=tmp_path)
    for i in range(10):
        record_action("app.example.com", "click", "text", i < 5, home=tmp_path)
    assert preferred_click_approach("app.example.com", tmp_path) == "ref"


def test_unknown_host_is_none(tmp_path):
    assert preferred_click_approach("never-seen.com", tmp_path) is None
    assert preferred_click_approach("", tmp_path) is None


# --- steer hint (what surfaces in her context) ----------------------------


def test_steer_hint_names_winner_and_loser(tmp_path):
    for _ in range(4):
        record_action("site.com", "click", "text", True, home=tmp_path)
    for _ in range(4):
        record_action("site.com", "click", "coords", False, home=tmp_path)
    hint = steer_hint("site.com", tmp_path)
    assert "text" in hint and "worked best" in hint
    assert "avoid coords" in hint


def test_steer_hint_empty_without_evidence(tmp_path):
    record_action("q.com", "click", "text", True, home=tmp_path)
    assert steer_hint("q.com", tmp_path) == ""


def test_desktop_and_empty_hosts_not_recorded(tmp_path):
    record_action("desktop", "click", "coords", True, home=tmp_path)
    record_action("", "click", "text", True, home=tmp_path)
    assert steer_hint("desktop", tmp_path) == ""


# --- privacy: only host origin, never path/query --------------------------


def test_persistence_stores_only_host(tmp_path):
    for _ in range(3):
        record_action("bank.example.com", "click", "text", True, home=tmp_path)
    blob = (tmp_path / "computer" / "skill.json").read_text(encoding="utf-8")
    assert "bank.example.com" in blob
    # No path/query artifacts — we only ever pass host origins in.
    assert "?" not in blob and "/checkout" not in blob


def test_full_loop_record_then_steer(tmp_path):
    # End-to-end: repeated wins for one approach → it becomes the preference.
    for _ in range(MIN_EVIDENCE + 1):
        record_action("store.com", "click", "ref", True, home=tmp_path)
    assert preferred_click_approach("store.com", tmp_path) == "ref"
    assert "ref" in steer_hint("store.com", tmp_path)


# --- mastery becomes part of who she is (organism lesson) ------------------

from remedy.core.computer.computer_skill import maybe_site_lesson  # noqa: E402


def test_mastery_lesson_fires_once(tmp_path):
    # 6 clean text clicks on a site → a "learned to drive it" lesson, once.
    for _ in range(6):
        record_action("gmail.com", "click", "text", True, home=tmp_path)
    lesson = maybe_site_lesson("gmail.com", tmp_path)
    assert lesson and lesson["tree"] == "computer" and lesson["outcome"] == "green"
    assert "gmail.com" in lesson["summary"] and "text" in lesson["summary"]
    # Second call does not re-fire (milestone flag set).
    assert maybe_site_lesson("gmail.com", tmp_path) is None


def test_no_mastery_lesson_below_threshold(tmp_path):
    for _ in range(4):  # < MASTERY_MIN_ACTIONS
        record_action("slow.com", "click", "text", True, home=tmp_path)
    assert maybe_site_lesson("slow.com", tmp_path) is None


def test_no_mastery_lesson_when_unreliable(tmp_path):
    # Lots of clicks but shaky success → not mastered.
    for i in range(12):
        record_action("flaky.com", "click", "text", i % 2 == 0, home=tmp_path)
    assert maybe_site_lesson("flaky.com", tmp_path) is None


def test_mastery_lesson_lands_in_soul(tmp_path):
    from remedy.memory.soul.field import clear_soul_cache, load_soul_field
    from remedy.memory.soul.update import record_self_inject_lesson

    clear_soul_cache()
    for _ in range(6):
        record_action("shop.com", "click", "ref", True, home=tmp_path)
    lesson = maybe_site_lesson("shop.com", tmp_path)
    assert lesson
    record_self_inject_lesson(home=tmp_path, outcome=lesson["outcome"],
                              tree=lesson["tree"], summary=lesson["summary"],
                              gate_detail=lesson["gate_detail"])
    sf = load_soul_field(tmp_path)
    assert any(x.tree == "computer" for x in sf.organism_lessons)
    clear_soul_cache()
