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
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

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

    def record_compress(
        self,
        *,
        tokens_before: int,
        tokens_after: int,
        quality: dict[str, Any] | None = None,
        source: str = "compress",
    ) -> None:
        q = quality or {}
        with self._lock:
            self.compress_events.append(
                CompressEvent(
                    ts=time.time(),
                    tokens_before=max(0, int(tokens_before)),
                    tokens_after=max(0, int(tokens_after)),
                    quality_score=float(q.get("score") or 0.0),
                    paths_kept=int(q.get("paths_kept") or 0),
                    paths_lost=int(q.get("paths_lost") or 0),
                    decisions_kept=int(q.get("decisions_kept") or 0),
                    decisions_lost=int(q.get("decisions_lost") or 0),
                    source=source,
                )
            )
            # Keep last 40 compress events
            if len(self.compress_events) > 40:
                self.compress_events = self.compress_events[-40:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            saved = 0
            q_scores: list[float] = []
            for e in self.compress_events:
                if e.tokens_before > e.tokens_after:
                    saved += e.tokens_before - e.tokens_after
                q_scores.append(e.quality_score)
            avg_q = sum(q_scores) / len(q_scores) if q_scores else None
            last = self.compress_events[-1] if self.compress_events else None
            # Stuck rate: signals per turn (capped display)
            stuck_rate = (
                self.stuck_signal_count / self.turns if self.turns > 0 else 0.0
            )
            re_rate = (
                self.re_explain_count / self.turns if self.turns > 0 else 0.0
            )
            return {
                "session_id": self.session_id,
                "turns": self.turns,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "tokens_total": self.tokens_in + self.tokens_out,
                "tokens_estimated_peak": self.tokens_estimated_peak,
                "tokens_saved_by_compress": saved,
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
                "last_compress": (
                    {
                        "tokens_before": last.tokens_before,
                        "tokens_after": last.tokens_after,
                        "delta": last.tokens_before - last.tokens_after,
                        "quality_score": last.quality_score,
                        "paths_kept": last.paths_kept,
                        "paths_lost": last.paths_lost,
                        "decisions_kept": last.decisions_kept,
                        "decisions_lost": last.decisions_lost,
                        "source": last.source,
                    }
                    if last
                    else None
                ),
                "uptime_s": round(time.time() - self.started_at, 1),
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
