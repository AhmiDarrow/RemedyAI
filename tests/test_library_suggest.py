"""Cache-first Skills Library suggest — rank, gates, suppress, snapshot."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from remedy.skills.library.suggest import (
    build_library_index,
    clear_session_suppress,
    invalidate_library_index,
    rank_library_skills,
    should_attempt_library_suggest,
    suggest_library_skill,
    suppress_suggest,
)


@pytest.fixture(autouse=True)
def _reset_index():
    invalidate_library_index()
    clear_session_suppress("test-sess")
    clear_session_suppress("s2")
    yield
    invalidate_library_index()


def test_chatty_and_short_no_attempt():
    assert should_attempt_library_suggest("hi", intent="chat") is False
    assert should_attempt_library_suggest("thanks", intent="tool") is False
    assert should_attempt_library_suggest("short", intent="tool") is False


def test_toolish_attempt():
    assert should_attempt_library_suggest(
        "Please help me review the CI pipeline and improve our tests for the PR",
        intent="tool",
    )


def test_rank_from_monorepo_or_cache():
    idx = build_library_index()
    # Dev machine has monorepo catalog or user cache; if neither, skip soft
    if len(idx) == 0:
        pytest.skip("no local skills catalog cache or monorepo catalog")
    t0 = time.perf_counter()
    hits = rank_library_skills(
        "conventional commits changelog pull request git hygiene",
        limit=5,
        min_score=0.2,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 50.0, f"rank too slow: {elapsed_ms:.1f}ms"
    # Second call should hit in-memory index
    t1 = time.perf_counter()
    _ = rank_library_skills("ci pipeline review github actions", limit=3, min_score=0.2)
    assert (time.perf_counter() - t1) * 1000 < 25.0
    assert isinstance(hits, list)


def test_installed_filtered():
    idx = build_library_index()
    if len(idx) == 0:
        pytest.skip("no catalog")
    sample = idx.entries[0]
    hits = rank_library_skills(
        sample.name.replace("-", " ") + " " + (sample.description or "")[:80],
        installed_names={sample.name, sample.id},
        min_score=0.15,
        limit=10,
    )
    ids = {h.id for h in hits}
    assert sample.id not in ids
    assert sample.name not in {h.name for h in hits}


def test_suppress_session():
    idx = build_library_index()
    if len(idx) == 0:
        pytest.skip("no catalog")
    hits = rank_library_skills(
        "write acceptance criteria for a new api feature testing",
        limit=3,
        min_score=0.15,
        session_id="test-sess",
    )
    if not hits:
        pytest.skip("no hits for fixture query")
    suppress_suggest("test-sess", hits[0].id)
    hits2 = rank_library_skills(
        "write acceptance criteria for a new api feature testing",
        limit=3,
        min_score=0.15,
        session_id="test-sess",
    )
    assert all(h.id != hits[0].id for h in hits2)


def test_suggest_marks_and_cover_threshold():
    idx = build_library_index()
    if len(idx) == 0:
        pytest.skip("no catalog")
    # High installed cover → no library suggest
    hit = suggest_library_skill(
        "implement conventional commits and changelog for pull request workflow",
        intent="tool",
        installed_top_score=0.9,
        session_id="s2",
        mark_suggested=True,
    )
    assert hit is None


def test_snapshot_may_emit_library_signal():
    from remedy.core.context_snapshot import build_context_snapshot
    from remedy.skills.library.suggest import clear_session_suppress

    clear_session_suppress("snap-lib")
    invalidate_library_index()
    idx = build_library_index()
    if len(idx) == 0:
        pytest.skip("no catalog")
    snap = build_context_snapshot(
        messages=[{"role": "user", "content": "q"}],
        user_text=(
            "Please review our CI pipeline and GitHub actions workflow "
            "for the pull request and improve test coverage"
        ),
        session_id="snap-lib",
    )
    # Signal optional if score below threshold, but must not error
    assert "library_suggest_error" not in (snap.signals or {})
    # Chatty
    snap2 = build_context_snapshot(
        messages=[],
        user_text="thanks",
        session_id="snap-lib2",
    )
    assert not (snap2.signals or {}).get("library_suggest")
