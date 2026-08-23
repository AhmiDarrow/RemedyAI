"""Monologue loop detection for local build (no triple 'I'll build…' stutter)."""

from __future__ import annotations

from remedy.core.local_agent_optimize import (
    looks_like_intent_monologue,
    monologue_fingerprint,
    project_listing_snapshot,
    text_has_internal_repetition,
)


def test_fingerprint_collapses_whitespace_and_repeats():
    a = "I'll build RemedyPDF. Let me start by exploring."
    b = "I'll build RemedyPDF.\n\nLet me start by exploring."
    assert monologue_fingerprint(a) == monologue_fingerprint(b)


def test_internal_repetition():
    t = (
        "I'll build RemedyPDF — sleek PDF viewer.\n"
        "Let me start by exploring the current project state.\n\n"
        "I'll build RemedyPDF — sleek PDF viewer.\n"
        "Let me start by exploring the current project state."
    )
    assert text_has_internal_repetition(t)
    assert not text_has_internal_repetition("Just one sentence about building.")


def test_collapse_hides_stutter_that_raw_detector_still_sees():
    """Loop must fingerprint the pre-collapse blob or looping mantras never trip."""
    from remedy.core.react_policy import collapse_repeated_sentences

    t = (
        "Core lib is green. Wiring strobe + calibrate UI, then committing. "
        * 6
    )
    assert text_has_internal_repetition(t)
    collapsed = collapse_repeated_sentences(t)
    assert not text_has_internal_repetition(collapsed)


def test_concatenated_stutter_without_space_is_repetition():
    """Session 765c 20:54: 'committing.Core lib is green.Core lib is green.'"""
    t = (
        "Core lib is green; wiring strobe + calibrate UI, expanding tests, then committing."
        "Core lib is green. Wiring strobe + calibrate UI, expanding tests, then committing."
        "Core engine is green. Wiring strobe + calibrate UI, expanding tests, then committing."
    )
    assert text_has_internal_repetition(t)


def test_intent_monologue_detect():
    assert looks_like_intent_monologue(
        "I'll build RemedyPDF — a sleek PDF viewer. Let me start by laying out the architecture."
    )


def test_listing_snapshot(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    snap = project_listing_snapshot(str(tmp_path))
    assert "README.md" in snap
    assert "main.py" in snap or "src" in snap
