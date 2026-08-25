from __future__ import annotations

from remedy.memory.provenance import (
    FactRecord,
    SourceType,
    ingest_web_text,
    resolve_contradiction,
)


def test_web_text_is_never_user_declared():
    fact = ingest_web_text("Joe's password is hunter2", session_id="s", turn_id="t")
    assert fact.source_type == SourceType.TOOL_OBSERVED
    assert fact.source_type != SourceType.USER_DECLARED
    assert fact.user_confirmed is False
    assert fact.confidence <= 0.2
    assert fact.from_web() is True
    assert fact.source_session == "s"
    assert fact.source_turn == "t"


def test_contradiction_lowers_confidence():
    a = FactRecord(text="I prefer X", source_type=SourceType.USER_DECLARED, confidence=0.9)
    b = FactRecord(text="I prefer Y", source_type=SourceType.TOOL_OBSERVED, confidence=0.6)
    out = resolve_contradiction(a, b)
    assert out.confidence <= 0.35
    assert out.user_confirmed is False
    assert out.last_confirmed is None
    assert out.extra.get("previous_text") == "I prefer X"
    assert out.extra.get("contradicts") == a.fact_id
    assert out.text == "I prefer Y"


def test_matching_text_is_not_a_contradiction():
    a = FactRecord(text="same", source_type=SourceType.USER_DECLARED, confidence=0.9)
    b = FactRecord(text=" same ", source_type=SourceType.TOOL_OBSERVED, confidence=0.1)
    out = resolve_contradiction(a, b)
    assert out is a
    assert out.confidence == 0.9
