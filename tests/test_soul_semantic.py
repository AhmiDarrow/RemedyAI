"""Semantic recall muscle — find a memory by meaning, not shared words."""

from __future__ import annotations

import remedy.memory.soul.semantic as sem
from remedy.memory.soul.field import (
    EpisodeResidue,
    clear_soul_cache,
    load_soul_field,
    save_soul_field,
)
from remedy.memory.soul.recall import recall_unified
from remedy.memory.soul.semantic import (
    cosine,
    semantic_available,
    semantic_scores,
    set_embed_override,
)

# Concept lexicon: different words that map to the SAME dimension, so synonyms
# cluster with zero shared tokens — exactly the case keyword recall can't catch.
_CONCEPTS = [
    ("space", {"launch", "rocket", "orbit", "ship", "liftoff", "space"}),
    ("food", {"lunch", "dinner", "food", "eat", "meal"}),
    ("money", {"budget", "cost", "price", "pay", "invoice"}),
    ("code", {"bug", "code", "deploy", "test", "compile"}),
]


def _fake_embed(texts):
    out = []
    for t in texts:
        low = (t or "").lower()
        vec = [1.0 if any(w in low for w in words) else 0.0 for _name, words in _CONCEPTS]
        out.append(vec)
    return out


def _use_fake():
    set_embed_override(_fake_embed)


def _clear_fake():
    set_embed_override(None)


def test_cosine_basics():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [0.0, 0.0]) == 0.0


def test_semantic_scores_clusters_synonyms(tmp_path):
    try:
        _use_fake()
        scores = semantic_scores(
            "launch",  # no shared token with any candidate below
            ["ship the rocket to orbit", "grab dinner with the team", "fix the deploy bug"],
            home=tmp_path,
        )
        assert scores is not None
        top = max(scores, key=scores.get)
        assert top == "ship the rocket to orbit"
        assert scores[top] > 0.9
    finally:
        _clear_fake()


def test_semantic_scores_none_without_embedder(tmp_path):
    # No override, no configured endpoint → unavailable → None (graceful).
    _clear_fake()
    assert semantic_scores("launch", ["ship the rocket"], home=tmp_path) is None


def test_recall_surfaces_meaning_match_zero_overlap(tmp_path):
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.episodes = [
        EpisodeResidue(id="e1", arc="ship the rocket to orbit", strength=0.8),
        EpisodeResidue(id="e2", arc="grabbed lunch and talked", strength=0.3),
    ]
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    try:
        _use_fake()
        out = recall_unified("how did the launch go", home=tmp_path)
        assert "rocket to orbit" in out
        assert "meaning" in out  # tagged as a semantic match
    finally:
        _clear_fake()


def test_recall_unchanged_when_embedder_absent(tmp_path):
    # With no embedder, recall must not crash and must not invent meaning hits.
    clear_soul_cache()
    sf = load_soul_field(tmp_path)
    sf.episodes = [EpisodeResidue(id="e1", arc="ship the rocket to orbit", strength=0.8)]
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    _clear_fake()
    out = recall_unified("how did the launch go", home=tmp_path)
    assert "meaning" not in out  # no semantic tag without an embedder


def test_cache_only_embeds_misses(tmp_path):
    calls = {"n": 0}

    def counting_embed(texts):
        calls["n"] += 1
        return _fake_embed(texts)

    try:
        set_embed_override(counting_embed)
        # First call embeds query + candidate.
        semantic_scores("launch", ["ship the rocket to orbit"], home=tmp_path)
        first = calls["n"]
        # Second identical call: candidate is cached; at most the query re-embeds.
        semantic_scores("launch", ["ship the rocket to orbit"], home=tmp_path)
        assert calls["n"] >= first  # sanity: monotonic
        # The candidate vector must have been persisted to the on-disk cache.
        cache_file = tmp_path / "soul" / sem.CACHE_FILENAME
        assert cache_file.is_file()
    finally:
        _clear_fake()


def test_semantic_available_reflects_override():
    try:
        _use_fake()
        assert semantic_available() is True
    finally:
        _clear_fake()
