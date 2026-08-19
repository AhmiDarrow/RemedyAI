"""The human bar, measured.

"Passes for human" is a gate with numbers behind it, not a vibe. The giveaway
on a phone call is almost never synthesis quality — it is the two-second silence
before she answers, or her talking over someone who had not finished.

Targets are the table in ``docs/TELEPHONY.md``. Anything that fails here fails
Phase 0, and we say so plainly rather than shipping something that sounds like
a machine and hoping nobody notices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HumanBar:
    """Acceptance thresholds. Tightening these is a product decision."""

    ttfa_p50_ms: float = 600.0
    ttfa_p95_ms: float = 1000.0
    barge_in_ms: float = 150.0
    false_interrupt_rate: float = 0.03
    false_wait_rate: float = 0.05
    dead_air_ms: float = 800.0
    #: Substantive speech may lag the backchannel, but not indefinitely.
    ttfs_p95_ms: float = 2200.0
    #: A turn counts as a "false wait" once the gap exceeds this.
    long_gap_ms: float = 1500.0


BAR = HumanBar()


@dataclass(slots=True)
class TurnRecord:
    """One exchange: they stopped, she started."""

    counterpart_end: float = 0.0
    #: First sound of any kind, backchannel included.
    her_first_audio: float = 0.0
    #: First *substantive* speech. A backchannel must not be able to game the
    #: bar: "mm-hm" then three seconds of nothing is not an answer.
    her_first_speech: float = 0.0
    #: She began speaking before they had finished.
    false_interrupt: bool = False
    #: A filler ("mm-hm", "one sec") covered the think time.
    filler_used: bool = False
    #: Longest uncovered silence inside this turn, in ms.
    dead_air_ms: float = 0.0

    @property
    def ttfa_ms(self) -> float:
        if not self.counterpart_end or not self.her_first_audio:
            return 0.0
        return max(0.0, (self.her_first_audio - self.counterpart_end) * 1000.0)

    @property
    def ttfs_ms(self) -> float:
        """Time to the actual answer, not to the noise that covered the gap."""
        if not self.counterpart_end or not self.her_first_speech:
            return 0.0
        return max(0.0, (self.her_first_speech - self.counterpart_end) * 1000.0)


@dataclass(slots=True)
class BargeInRecord:
    """They cut in; how fast did she stop?"""

    onset: float
    silenced: float = 0.0

    @property
    def latency_ms(self) -> float:
        if not self.silenced:
            return 0.0
        return max(0.0, (self.silenced - self.onset) * 1000.0)


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. No numpy — this runs in tests and on the line.

    ``ceil``, not ``round(k + 0.5)``: Python rounds halves to even, so that
    idiom lands one rank too high whenever ``k`` is an odd integer — the median
    of two turns came back as the slower one, and of six as the fourth.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


@dataclass(slots=True)
class CallMetrics:
    """Accumulates one call's timing, then judges it."""

    turns: list[TurnRecord] = field(default_factory=list)
    barge_ins: list[BargeInRecord] = field(default_factory=list)
    bar: HumanBar = BAR
    #: Backchannels over a pause that never became a turn. Not a failure.
    backchannels: int = 0

    # -- recording -----------------------------------------------------------

    def start_turn(self, counterpart_end: float) -> TurnRecord:
        rec = TurnRecord(counterpart_end=counterpart_end)
        self.turns.append(rec)
        return rec

    def start_barge_in(self, onset: float) -> BargeInRecord:
        rec = BargeInRecord(onset=onset)
        self.barge_ins.append(rec)
        return rec

    # -- judging -------------------------------------------------------------

    @property
    def ttfa(self) -> list[float]:
        return [t.ttfa_ms for t in self.turns if t.ttfa_ms > 0]

    @property
    def ttfs(self) -> list[float]:
        return [t.ttfs_ms for t in self.turns if t.ttfs_ms > 0]

    @property
    def false_interrupt_rate(self) -> float:
        if not self.turns:
            return 0.0
        return sum(1 for t in self.turns if t.false_interrupt) / len(self.turns)

    @property
    def unanswered(self) -> list[TurnRecord]:
        """Turns she took and never actually answered.

        Invisible to every percentile — ``ttfs`` has no sample for a turn with
        no speech in it — so counting them nowhere let total silence score
        better than a slow answer: five turns, one answered, and the call
        passed clean.
        """
        return [t for t in self.turns if not t.her_first_speech]

    @property
    def false_wait_rate(self) -> float:
        if not self.turns:
            return 0.0
        # Judged on the answer, not on the filler that preceded it. No answer at
        # all is the longest wait there is, not an absent measurement.
        late = sum(
            1
            for t in self.turns
            if not t.her_first_speech or t.ttfs_ms > self.bar.long_gap_ms
        )
        return late / len(self.turns)

    @property
    def worst_dead_air_ms(self) -> float:
        return max((t.dead_air_ms for t in self.turns), default=0.0)

    def summary(self) -> dict[str, Any]:
        t = self.ttfa
        return {
            "turns": len(self.turns),
            "ttfa_p50_ms": round(percentile(t, 50), 1),
            "ttfa_p95_ms": round(percentile(t, 95), 1),
            "ttfa_max_ms": round(max(t, default=0.0), 1),
            "ttfs_p50_ms": round(percentile(self.ttfs, 50), 1),
            "ttfs_p95_ms": round(percentile(self.ttfs, 95), 1),
            "backchannels": self.backchannels,
            "barge_ins": len(self.barge_ins),
            "barge_in_p95_ms": round(
                percentile([b.latency_ms for b in self.barge_ins], 95), 1
            ),
            "false_interrupt_rate": round(self.false_interrupt_rate, 4),
            "false_wait_rate": round(self.false_wait_rate, 4),
            "worst_dead_air_ms": round(self.worst_dead_air_ms, 1),
            "fillers_used": sum(1 for x in self.turns if x.filler_used),
            "unanswered": len(self.unanswered),
        }

    def failures(self) -> list[str]:
        """Plain sentences for every threshold missed. Empty means it passed."""
        out: list[str] = []
        t = self.ttfa
        if not t:
            return ["she never spoke — no turns to measure"]
        p50, p95 = percentile(t, 50), percentile(t, 95)
        if p50 > self.bar.ttfa_p50_ms:
            out.append(
                f"answers too slowly: median {p50:.0f} ms, bar {self.bar.ttfa_p50_ms:.0f} ms"
            )
        if p95 > self.bar.ttfa_p95_ms:
            out.append(
                f"worst answers drag: p95 {p95:.0f} ms, bar {self.bar.ttfa_p95_ms:.0f} ms"
            )
        sp95 = percentile(self.ttfs, 95)
        if sp95 > self.bar.ttfs_p95_ms:
            out.append(
                f"the answer itself lands late: p95 {sp95:.0f} ms, "
                f"bar {self.bar.ttfs_p95_ms:.0f} ms"
            )
        if self.barge_ins:
            bp95 = percentile([b.latency_ms for b in self.barge_ins], 95)
            if bp95 > self.bar.barge_in_ms:
                out.append(
                    f"keeps talking when interrupted: p95 {bp95:.0f} ms, "
                    f"bar {self.bar.barge_in_ms:.0f} ms"
                )
        if self.false_interrupt_rate > self.bar.false_interrupt_rate:
            out.append(
                f"talks over people: {self.false_interrupt_rate:.1%}, "
                f"bar {self.bar.false_interrupt_rate:.1%}"
            )
        if self.false_wait_rate > self.bar.false_wait_rate:
            out.append(
                f"leaves long gaps: {self.false_wait_rate:.1%}, "
                f"bar {self.bar.false_wait_rate:.1%}"
            )
        silent = self.unanswered
        if silent:
            out.append(
                f"{len(silent)} of {len(self.turns)} turns got no answer at all"
            )
        if self.worst_dead_air_ms > self.bar.dead_air_ms:
            out.append(
                f"uncovered silence of {self.worst_dead_air_ms:.0f} ms, "
                f"bar {self.bar.dead_air_ms:.0f} ms"
            )
        return out

    @property
    def passed(self) -> bool:
        return not self.failures()
