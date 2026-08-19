"""The human bar itself — the thing that decides whether Phase 0 passed.

A gate is only worth having if it fails the calls it should. These lock the
arithmetic, not the thresholds: the thresholds are a product decision, but
"5 turns, 1 answered" must never come back clean.
"""

from __future__ import annotations

import pytest

from remedy.voice.realtime.metrics import (
    BargeInRecord,
    CallMetrics,
    HumanBar,
    TurnRecord,
    percentile,
)


def _answered(gap_ms: float, *, at: float = 1.0) -> TurnRecord:
    return TurnRecord(
        counterpart_end=at,
        her_first_audio=at + gap_ms / 1000.0,
        her_first_speech=at + gap_ms / 1000.0,
    )


def _unanswered(at: float = 1.0) -> TurnRecord:
    return TurnRecord(counterpart_end=at)


@pytest.mark.parametrize(
    ("n", "expect_p50"),
    # ``round(k + 0.5)`` lands one rank high whenever k is an odd integer,
    # because Python rounds halves to even: n=2, 6 and 10 all used to be wrong.
    [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (6, 3), (7, 4), (8, 4), (10, 5)],
)
def test_median_is_the_nearest_rank_median(n, expect_p50):
    assert percentile(list(range(1, n + 1)), 50) == expect_p50


def test_the_median_of_two_is_not_the_slower_one():
    assert percentile([100.0, 900.0], 50) == 100.0


def test_p95_takes_the_worst_of_a_small_sample():
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 95) == 5.0


def test_percentile_of_nothing_is_zero():
    assert percentile([], 50) == 0.0


def test_a_turn_she_never_answered_fails_the_call():
    """It has no latency to measure, so every percentile skips it. Counted
    nowhere, total silence scored better than a slow answer."""
    m = CallMetrics()
    m.turns.append(_answered(300.0))
    m.turns.extend(_unanswered(10.0 + i) for i in range(4))

    assert len(m.unanswered) == 4
    assert m.false_wait_rate == pytest.approx(0.8)
    assert not m.passed
    assert any("no answer at all" in f for f in m.failures())


def test_a_call_she_answered_throughout_passes():
    m = CallMetrics()
    m.turns.extend(_answered(300.0, at=float(i)) for i in range(5))
    assert m.unanswered == []
    assert m.false_wait_rate == 0.0
    assert m.passed, m.failures()


def test_the_summary_reports_the_turns_that_got_nothing():
    m = CallMetrics()
    m.turns.append(_answered(300.0))
    m.turns.append(_unanswered(9.0))
    assert m.summary()["unanswered"] == 1


def test_a_backchannel_alone_is_not_an_answer():
    """"mm-hm" and then nothing is exactly what the bar exists to catch."""
    m = CallMetrics()
    rec = TurnRecord(counterpart_end=1.0, her_first_audio=1.3, filler_used=True)
    m.turns.append(rec)
    assert rec.ttfa_ms == pytest.approx(300.0)
    assert rec.ttfs_ms == 0.0
    assert m.unanswered == [rec]
    assert not m.passed


def test_a_silent_call_says_so_plainly():
    assert CallMetrics().failures() == ["she never spoke — no turns to measure"]


def test_barge_in_is_judged_only_when_one_happened():
    m = CallMetrics(bar=HumanBar(barge_in_ms=10.0))
    m.turns.append(_answered(300.0))
    assert m.passed
    m.barge_ins.append(BargeInRecord(onset=1.0, silenced=1.5))
    assert not m.passed
    assert any("interrupted" in f for f in m.failures())
