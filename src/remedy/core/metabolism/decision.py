"""Decision currency — DU (decision units) and waste scoring alongside tokens.

Metabolism optimizes evidence/decision units per success, not raw token worship.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionUnit:
    kind: str  # plan | approve | tool_write | prefer | pin | mission | tier
    summary: str
    ts: float = field(default_factory=time.time)
    session_id: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary[:240],
            "ts": self.ts,
        }


@dataclass
class DecisionTracker:
    session_id: str = ""
    decision_units: int = 0
    units: list[DecisionUnit] = field(default_factory=list)
    waste_tool_batches: int = 0  # batches with 0 EU
    productive_tool_batches: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        kind: str,
        summary: str,
    ) -> DecisionUnit:
        du = DecisionUnit(
            kind=kind,
            summary=(summary or "")[:240],
            session_id=self.session_id,
        )
        with self._lock:
            self.units.append(du)
            self.decision_units += 1
            if len(self.units) > 200:
                self.units = self.units[-200:]
        return du

    def record_tool_batch(self, *, new_eu: int, tokens_est: int = 0) -> None:
        with self._lock:
            if new_eu > 0:
                self.productive_tool_batches += 1
            else:
                self.waste_tool_batches += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total_b = self.productive_tool_batches + self.waste_tool_batches
            waste_rate = (
                self.waste_tool_batches / total_b if total_b else 0.0
            )
            return {
                "session_id": self.session_id,
                "decision_units": self.decision_units,
                "productive_tool_batches": self.productive_tool_batches,
                "waste_tool_batches": self.waste_tool_batches,
                "waste_batch_rate": round(waste_rate, 4),
                "recent": [u.to_public() for u in self.units[-8:]],
            }


_trackers: dict[str, DecisionTracker] = {}
_lock = threading.Lock()


def get_decision_tracker(session_id: str | None = None) -> DecisionTracker:
    key = (session_id or "").strip() or "_default"
    with _lock:
        if key not in _trackers:
            _trackers[key] = DecisionTracker(session_id=key)
        return _trackers[key]


def reset_decision_tracker(session_id: str | None = None) -> None:
    key = (session_id or "").strip() or "_default"
    with _lock:
        _trackers.pop(key, None)
