"""Partner Time Crystal — multi-horizon memory promotion.

Horizons: turn → session → project_week → life
Secrets never promote. Per-tab isolation via session_id.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HORIZONS = ("turn", "session", "project_week", "life")


@dataclass
class CrystalFact:
    text: str
    horizon: str
    source: str = ""
    project_id: str = ""
    session_id: str = ""
    hits: int = 1
    ts: float = field(default_factory=time.time)

    def to_public(self) -> dict[str, Any]:
        return {
            "text": self.text[:400],
            "horizon": self.horizon,
            "source": self.source,
            "project_id": self.project_id,
            "hits": self.hits,
            "ts": self.ts,
        }


@dataclass
class TimeCrystal:
    session_id: str = ""
    project_id: str = ""
    facts: list[CrystalFact] = field(default_factory=list)
    promotions: int = 0
    blocked_secret: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def admit(
        self,
        text: str,
        *,
        horizon: str = "session",
        source: str = "",
        project_id: str = "",
    ) -> CrystalFact | None:
        t = (text or "").strip()
        if not t or len(t) < 4:
            return None
        from remedy.core.metabolism.redact import looks_like_secret_text

        if looks_like_secret_text(t):
            with self._lock:
                self.blocked_secret += 1
            return None
        if horizon not in HORIZONS:
            horizon = "session"
        # Dedupe by lower text
        key = t.lower()[:200]
        with self._lock:
            for f in self.facts:
                if f.text.lower()[:200] == key and f.horizon == horizon:
                    f.hits += 1
                    f.ts = time.time()
                    return f
            fact = CrystalFact(
                text=t[:400],
                horizon=horizon,
                source=source,
                project_id=project_id or self.project_id,
                session_id=self.session_id,
            )
            self.facts.append(fact)
            if len(self.facts) > 300:
                self.facts = self.facts[-300:]
            return fact

    def promote_session_to_project(self, *, min_hits: int = 2) -> int:
        """Repeated session decisions → project_week."""
        n = 0
        with self._lock:
            for f in self.facts:
                if f.horizon == "session" and f.hits >= min_hits:
                    f.horizon = "project_week"
                    self.promotions += 1
                    n += 1
        return n

    def promote_pins_to_life(self, texts: list[str]) -> int:
        """Explicit pins → life horizon."""
        n = 0
        for t in texts or []:
            if self.admit(t, horizon="life", source="pin"):
                n += 1
                with self._lock:
                    self.promotions += 1
        return n

    def hot_block(self, *, max_chars: int = 1200) -> str:
        """Always-hot life + project_week + session heads for injection.

        Cached until next admit (avoids re-sort every inject).
        """
        with self._lock:
            cache_key = (max_chars, len(self.facts), self.promotions)
            cached = getattr(self, "_hot_cache", None)
            if (
                isinstance(cached, tuple)
                and cached[0] == cache_key
                and isinstance(cached[1], str)
            ):
                return cached[1]
            order = {"life": 0, "project_week": 1, "session": 2, "turn": 3}
            ordered = sorted(
                self.facts,
                key=lambda f: (order.get(f.horizon, 9), -f.hits, -f.ts),
            )
            lines: list[str] = []
            used = 0
            for f in ordered:
                if f.horizon == "turn":
                    continue
                line = f"- ({f.horizon}) {f.text}"
                if used + len(line) > max_chars:
                    break
                lines.append(line)
                used += len(line) + 1
            if not lines:
                out = ""
            else:
                out = "[Time Crystal]\n" + "\n".join(lines)
            self._hot_cache = (cache_key, out)
            return out

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            by_h = {h: 0 for h in HORIZONS}
            for f in self.facts:
                by_h[f.horizon] = by_h.get(f.horizon, 0) + 1
            return {
                "session_id": self.session_id,
                "project_id": self.project_id,
                "counts": by_h,
                "promotions": self.promotions,
                "blocked_secret": self.blocked_secret,
                "recent": [f.to_public() for f in self.facts[-10:]],
            }

    def export_durable(self) -> list[dict[str, Any]]:
        """Life + project_week only for portable identity."""
        with self._lock:
            return [
                f.to_public()
                for f in self.facts
                if f.horizon in ("life", "project_week")
            ]

    def persist(self, home: Path | str | None = None) -> Path | None:
        try:
            root = Path(home).expanduser() if home else Path.home() / ".remedy"
            d = root / "time_crystal"
            d.mkdir(parents=True, exist_ok=True)
            sid = "".join(
                c for c in (self.session_id or "default") if c.isalnum() or c in "-_"
            )[:48]
            path = d / f"{sid or 'default'}.json"
            path.write_text(
                json.dumps(self.snapshot(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return path
        except Exception:
            return None


_crystals: dict[str, TimeCrystal] = {}
_lock = threading.Lock()


def get_time_crystal(
    session_id: str | None = None,
    *,
    project_id: str = "",
) -> TimeCrystal:
    key = (session_id or "").strip() or "_default"
    with _lock:
        if key not in _crystals:
            _crystals[key] = TimeCrystal(session_id=key, project_id=project_id)
        elif project_id:
            _crystals[key].project_id = project_id
        return _crystals[key]


def reset_time_crystal(session_id: str | None = None) -> None:
    key = (session_id or "").strip() or "_default"
    with _lock:
        _crystals.pop(key, None)
