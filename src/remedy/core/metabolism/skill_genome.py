"""Skill genome — phenotype scores for procedures (promote / demote silently).

Genotype lives in skill markdown; phenotype is local outcome stats.
No noisy auto-learn of new skill bodies here — only rank signals.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bound phenotype map growth across long-lived processes / skill churn.
MAX_PHENOTYPES = 128


@dataclass
class SkillPhenotype:
    skill_id: str
    success: int = 0
    fail: int = 0
    latency_ms_sum: float = 0.0
    uses: int = 0
    protected: bool = False  # multi-success hard-won
    last_ts: float = field(default_factory=time.time)

    def record(self, *, ok: bool, latency_ms: float = 0.0) -> None:
        self.uses += 1
        self.last_ts = time.time()
        self.latency_ms_sum += max(0.0, float(latency_ms))
        if ok:
            self.success += 1
            if self.success >= 3 and self.fail == 0:
                self.protected = True
        else:
            if not self.protected:
                self.fail += 1

    @property
    def score(self) -> float:
        if self.uses <= 0:
            return 0.0
        rate = self.success / max(1, self.success + self.fail)
        # Prefer successful + used
        return round(rate * 100.0 + min(20.0, self.uses), 3)

    def to_public(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "success": self.success,
            "fail": self.fail,
            "uses": self.uses,
            "protected": self.protected,
            "score": self.score,
            "avg_latency_ms": round(
                self.latency_ms_sum / self.uses, 1
            )
            if self.uses
            else 0.0,
        }


@dataclass
class SkillGenome:
    phenotypes: dict[str, SkillPhenotype] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _prune_locked(self) -> None:
        """Drop lowest-score unprotected phenotypes when over MAX_PHENOTYPES."""
        if len(self.phenotypes) <= MAX_PHENOTYPES:
            return
        items = list(self.phenotypes.values())
        # Evict unprotected first, then lowest score / oldest last_ts.
        items.sort(
            key=lambda p: (
                1 if p.protected else 0,
                p.score,
                p.last_ts,
            )
        )
        keep = items[-MAX_PHENOTYPES:]
        self.phenotypes = {p.skill_id: p for p in keep}

    def record(
        self,
        skill_id: str,
        *,
        ok: bool,
        latency_ms: float = 0.0,
    ) -> SkillPhenotype:
        sid = (skill_id or "").strip()
        if not sid:
            sid = "unknown"
        with self._lock:
            ph = self.phenotypes.get(sid)
            if ph is None:
                ph = SkillPhenotype(skill_id=sid)
                self.phenotypes[sid] = ph
            ph.record(ok=ok, latency_ms=latency_ms)
            if len(self.phenotypes) > MAX_PHENOTYPES:
                self._prune_locked()
            return ph

    def rank(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(
                self.phenotypes.values(),
                key=lambda p: (-p.score, -p.uses),
            )
            return [p.to_public() for p in items[:limit]]

    def snapshot(self, *, lean: bool = False) -> dict[str, Any]:
        # Do not call rank() while holding _lock (Lock is not re-entrant).
        with self._lock:
            count = len(self.phenotypes)
            if lean:
                return {"count": count}
            items = sorted(
                self.phenotypes.values(),
                key=lambda p: (-p.score, -p.uses),
            )
            top = [p.to_public() for p in items[:8]]
        return {"count": count, "top": top}

    def persist(self, home: Path | str | None = None) -> Path | None:
        try:
            import os
            import tempfile

            root = Path(home).expanduser() if home else Path.home() / ".remedy"
            d = root / "skill_genome"
            d.mkdir(parents=True, exist_ok=True)
            path = d / "phenotypes.json"
            with self._lock:
                data = {
                    "version": 1,
                    "skills": {k: v.to_public() for k, v in self.phenotypes.items()},
                }
            payload = json.dumps(data, indent=2)
            # Atomic replace so concurrent readers never see a half-written file
            fd, tmp_name = tempfile.mkstemp(prefix=".phenotypes.", suffix=".tmp", dir=str(d))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, path)
            except Exception:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            return path
        except Exception:
            return None


_genome = SkillGenome()
_lock = threading.Lock()


def get_skill_genome() -> SkillGenome:
    return _genome


def reset_skill_genome() -> None:
    global _genome
    with _lock:
        _genome = SkillGenome()
