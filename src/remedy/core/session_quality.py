"""Real-session quality baselines for Memory Harness / Remedy continuity.

Tracks (per process / session key):
  - token totals before/after compress
  - stuck signals (repeated compress pressure, fail loops)
  - re-explain signals (user restates prior constraints)

Designed for silent product metrics — not a user-facing bot dashboard.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_RE_EXPLAIN = re.compile(
    r"\b("
    r"i already (told|said|mentioned)|"
    r"as i (said|mentioned|told you)|"
    r"like i (said|told you)|"
    r"again[,:]?\s*(please|i need)|"
    r"you (already|just) (know|have)|"
    r"don'?t (make me )?repeat|"
    r"still (using|doing) the wrong|"
    r"i (said|told) you (to|not|already)"
    r")\b",
    re.I,
)

_STUCK_PHRASES = re.compile(
    r"\b("
    r"stuck|going in circles|same error again|"
    r"try(ing)? again|still failing|doesn'?t work still|"
    r"why (are you|did you) (do|doing) that again"
    r")\b",
    re.I,
)


@dataclass
class CompressEvent:
    ts: float
    tokens_before: int
    tokens_after: int
    quality_score: float
    paths_kept: int
    paths_lost: int
    decisions_kept: int
    decisions_lost: int
    source: str = "compress"


@dataclass
class SessionQuality:
    """In-memory tracker; one instance per agent/runtime or global registry key."""

    session_id: str = ""
    turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_estimated_peak: int = 0
    compress_events: list[CompressEvent] = field(default_factory=list)
    soft_nudge_count: int = 0
    strong_nudge_count: int = 0
    re_explain_count: int = 0
    stuck_signal_count: int = 0
    tool_fail_streak: int = 0
    max_tool_fail_streak: int = 0
    last_user_text: str = ""
    started_at: float = field(default_factory=time.time)
    # Metabolism (silent partner OS) — Advanced / harness only
    last_tier: int = 1
    evidence_units: int = 0
    decision_units: int = 0
    waste_tokens: int = 0
    force_spread_count: int = 0
    shadow_catch_count: int = 0
    verify_catch_count: int = 0
    ir_step_count: int = 0
    # Running compress aggregates — snapshot() is O(1), not O(events)
    _tokens_saved_by_compress: int = field(default=0, repr=False)
    _quality_score_sum: float = field(default=0.0, repr=False)
    _quality_score_n: int = field(default=0, repr=False)
    _last_compress_pub: dict[str, Any] | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def needs_remedy_signals(self) -> bool:
        """Cheap hot-path check: any rates that could fire quality remedies?"""
        with self._lock:
            if self.re_explain_count >= 1 or self.stuck_signal_count >= 1:
                return True
            if self.max_tool_fail_streak >= 2:
                return True
            if self._last_compress_pub is not None:
                qs = self._last_compress_pub.get("quality_score")
                if qs is not None and float(qs) < 0.55:
                    return True
            return False

    def record_turn(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        estimated_context: int = 0,
        user_text: str = "",
    ) -> None:
        with self._lock:
            self.turns += 1
            self.tokens_in += max(0, int(prompt_tokens))
            self.tokens_out += max(0, int(completion_tokens))
            if estimated_context > self.tokens_estimated_peak:
                self.tokens_estimated_peak = int(estimated_context)
            ut = (user_text or "").strip()
            if ut:
                if _RE_EXPLAIN.search(ut):
                    self.re_explain_count += 1
                if _STUCK_PHRASES.search(ut):
                    self.stuck_signal_count += 1
                self.last_user_text = ut[:500]

    def record_nudge(self, level: str) -> None:
        with self._lock:
            if level == "strong":
                self.strong_nudge_count += 1
            elif level == "soft":
                self.soft_nudge_count += 1

    def record_tool_result(self, *, success: bool) -> None:
        with self._lock:
            if success:
                self.tool_fail_streak = 0
            else:
                self.tool_fail_streak += 1
                if self.tool_fail_streak > self.max_tool_fail_streak:
                    self.max_tool_fail_streak = self.tool_fail_streak
                if self.tool_fail_streak >= 3:
                    self.stuck_signal_count += 1

    def record_metabolism(
        self,
        *,
        tier: int | None = None,
        evidence_units: int | None = None,
        decision_units: int | None = None,
        waste_tokens: int | None = None,
        force_spread: bool = False,
        ir_steps: int = 0,
    ) -> None:
        """Update silent metabolism counters (EU/DU/tier/IR)."""
        with self._lock:
            if tier is not None:
                self.last_tier = int(tier)
            if evidence_units is not None:
                self.evidence_units = max(self.evidence_units, int(evidence_units))
            if decision_units is not None:
                self.decision_units = max(self.decision_units, int(decision_units))
            if waste_tokens is not None:
                self.waste_tokens = max(self.waste_tokens, int(waste_tokens))
            if force_spread:
                self.force_spread_count += 1
            if ir_steps:
                self.ir_step_count += int(ir_steps)

    def record_shadow_catch(self) -> None:
        with self._lock:
            self.shadow_catch_count += 1

    def record_verify(self, *, caught: bool = True) -> None:
        with self._lock:
            if caught:
                self.verify_catch_count += 1

    def record_compress(
        self,
        *,
        tokens_before: int,
        tokens_after: int,
        quality: dict[str, Any] | None = None,
        source: str = "compress",
    ) -> None:
        q = quality or {}
        tb = max(0, int(tokens_before))
        ta = max(0, int(tokens_after))
        score = float(q.get("score") or 0.0)
        ev = CompressEvent(
            ts=time.time(),
            tokens_before=tb,
            tokens_after=ta,
            quality_score=score,
            paths_kept=int(q.get("paths_kept") or 0),
            paths_lost=int(q.get("paths_lost") or 0),
            decisions_kept=int(q.get("decisions_kept") or 0),
            decisions_lost=int(q.get("decisions_lost") or 0),
            source=source,
        )
        with self._lock:
            self.compress_events.append(ev)
            if tb > ta:
                self._tokens_saved_by_compress += tb - ta
            self._quality_score_sum += score
            self._quality_score_n += 1
            # Keep last 40 compress events; recompute running totals if trimmed
            if len(self.compress_events) > 40:
                dropped = self.compress_events[:-40]
                self.compress_events = self.compress_events[-40:]
                for d in dropped:
                    if d.tokens_before > d.tokens_after:
                        self._tokens_saved_by_compress -= (
                            d.tokens_before - d.tokens_after
                        )
                    self._quality_score_sum -= d.quality_score
                    self._quality_score_n -= 1
                if self._tokens_saved_by_compress < 0:
                    self._tokens_saved_by_compress = 0
                if self._quality_score_n < 0:
                    self._quality_score_n = 0
                    self._quality_score_sum = 0.0
            self._last_compress_pub = {
                "tokens_before": ev.tokens_before,
                "tokens_after": ev.tokens_after,
                "delta": ev.tokens_before - ev.tokens_after,
                "quality_score": ev.quality_score,
                "paths_kept": ev.paths_kept,
                "paths_lost": ev.paths_lost,
                "decisions_kept": ev.decisions_kept,
                "decisions_lost": ev.decisions_lost,
                "source": ev.source,
            }

    def snapshot(self) -> dict[str, Any]:
        """O(1) public snapshot (running compress aggregates; no event walk)."""
        with self._lock:
            stuck_rate = (
                self.stuck_signal_count / self.turns if self.turns > 0 else 0.0
            )
            re_rate = (
                self.re_explain_count / self.turns if self.turns > 0 else 0.0
            )
            avg_q = (
                self._quality_score_sum / self._quality_score_n
                if self._quality_score_n > 0
                else None
            )
            last = self._last_compress_pub
            return {
                "session_id": self.session_id,
                "turns": self.turns,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "tokens_total": self.tokens_in + self.tokens_out,
                "tokens_estimated_peak": self.tokens_estimated_peak,
                "tokens_saved_by_compress": self._tokens_saved_by_compress,
                "compress_count": len(self.compress_events),
                "soft_nudge_count": self.soft_nudge_count,
                "strong_nudge_count": self.strong_nudge_count,
                "re_explain_count": self.re_explain_count,
                "re_explain_rate": round(re_rate, 4),
                "stuck_signal_count": self.stuck_signal_count,
                "stuck_rate": round(stuck_rate, 4),
                "max_tool_fail_streak": self.max_tool_fail_streak,
                "avg_compress_quality": (
                    round(avg_q, 3) if avg_q is not None else None
                ),
                "last_compress": dict(last) if last else None,
                "uptime_s": round(time.time() - self.started_at, 1),
                # Metabolism (Advanced / harness — not Simple UI chrome)
                "metabolism": {
                    "last_tier": self.last_tier,
                    "evidence_units": self.evidence_units,
                    "decision_units": self.decision_units,
                    "waste_tokens": self.waste_tokens,
                    "force_spread_count": self.force_spread_count,
                    "shadow_catch_count": self.shadow_catch_count,
                    "verify_catch_count": self.verify_catch_count,
                    "ir_step_count": self.ir_step_count,
                },
            }


_trackers: dict[str, SessionQuality] = {}
_trackers_lock = threading.Lock()


def get_session_quality(session_id: str | None = None) -> SessionQuality:
    key = (session_id or "").strip() or "_default"
    with _trackers_lock:
        if key not in _trackers:
            _trackers[key] = SessionQuality(session_id=key)
        return _trackers[key]


def reset_session_quality(session_id: str | None = None) -> None:
    key = (session_id or "").strip() or "_default"
    with _trackers_lock:
        _trackers.pop(key, None)
