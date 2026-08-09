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
