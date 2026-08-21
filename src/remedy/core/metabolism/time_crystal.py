"""Partner Time Crystal — multi-horizon memory promotion.

Horizons: turn → session → project_week → life
Secrets never promote. Per-tab isolation via session_id.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.home import default_home

HORIZONS = ("turn", "session", "project_week", "life")
# Bound fact list growth (was 300) — hot_block sort + inject size stay cheap.
MAX_CRYSTAL_FACTS = 128


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
    # Bumps on admit / hit / promote so hot_block cache cannot serve stale order.
    _rev: int = field(default=0, repr=False)
    _persist_rev: int = field(default=-1, repr=False)
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
                    self._rev += 1
                    return f
            fact = CrystalFact(
                text=t[:400],
                horizon=horizon,
                source=source,
                project_id=project_id or self.project_id,
                session_id=self.session_id,
            )
            self.facts.append(fact)
            if len(self.facts) > MAX_CRYSTAL_FACTS:
                # Prefer durable horizons when trimming: drop oldest turn/session first.
                self._trim_facts_locked()
            self._rev += 1
            return fact

    def _trim_facts_locked(self) -> None:
        """Keep <= MAX_CRYSTAL_FACTS: durable first, then newest session/turn.

        Newest admits must survive trim so hit-dedupe still works on the hot path.
        """
        if len(self.facts) <= MAX_CRYSTAL_FACTS:
            return
        durable = [f for f in self.facts if f.horizon in ("life", "project_week")]
        rest = [f for f in self.facts if f.horizon not in ("life", "project_week")]
        if len(durable) > MAX_CRYSTAL_FACTS:
            durable.sort(
                key=lambda f: (
                    0 if f.horizon == "life" else 1,
                    -f.hits,
                    -f.ts,
                )
            )
            self.facts = durable[:MAX_CRYSTAL_FACTS]
            return
        budget = MAX_CRYSTAL_FACTS - len(durable)
        # Preserve insertion order: drop oldest rest first
        self.facts = durable + rest[-budget:]

    def promote_session_to_project(self, *, min_hits: int = 2) -> int:
        """Repeated session decisions → project_week."""
        n = 0
        with self._lock:
            for f in self.facts:
                if f.horizon == "session" and f.hits >= min_hits:
                    f.horizon = "project_week"
                    self.promotions += 1
                    n += 1
            if n:
                self._rev += 1
        return n

    def promote_pins_to_life(self, texts: list[str]) -> int:
        """Explicit pins → life horizon."""
        n = 0
        for t in texts or []:
            if self.admit(t, horizon="life", source="pin"):
                n += 1
                with self._lock:
                    self.promotions += 1
                    self._rev += 1
        return n

    def hot_block(self, *, max_chars: int = 1200) -> str:
        """Always-hot life + project_week + session heads for injection.

        Cached until next admit/hit/promote (avoids re-sort every inject).
        """
        with self._lock:
            cache_key = (max_chars, self._rev, len(self.facts), self.promotions)
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
            out = "" if not lines else "[Time Crystal]\n" + "\n".join(lines)
            self._hot_cache = (cache_key, out)
            return out

    def snapshot(self, *, lean: bool = False) -> dict[str, Any]:
        """Horizon counts. *lean* skips recent fact list copy."""
        with self._lock:
            by_h = dict.fromkeys(HORIZONS, 0)
            for f in self.facts:
                by_h[f.horizon] = by_h.get(f.horizon, 0) + 1
            out: dict[str, Any] = {
                "session_id": self.session_id,
                "project_id": self.project_id,
                "counts": by_h,
                "promotions": self.promotions,
                "blocked_secret": self.blocked_secret,
            }
            if not lean:
                out["recent"] = [f.to_public() for f in self.facts[-10:]]
            return out

    def export_durable(self) -> list[dict[str, Any]]:
        """Life + project_week only for portable identity."""
        with self._lock:
            return [
                f.to_public()
                for f in self.facts
                if f.horizon in ("life", "project_week")
            ]

    def persist(self, home: Path | str | None = None) -> Path | None:
        with self._lock:
            if self._rev == self._persist_rev:
                return None
            rev = self._rev
            payload = {
                "session_id": self.session_id,
                "project_id": self.project_id,
                "promotions": self.promotions,
                "blocked_secret": self.blocked_secret,
                "facts": [
                    {
                        "text": f.text,
                        "horizon": f.horizon,
                        "source": f.source,
                        "project_id": f.project_id,
                        "session_id": f.session_id,
                        "hits": f.hits,
                        "ts": f.ts,
                    }
                    for f in self.facts
                ],
                "durable": [
                    f.to_public()
                    for f in self.facts
                    if f.horizon in ("life", "project_week")
                ],
            }
        try:
            root = _crystal_root(home)
            d = root / "time_crystal"
            d.mkdir(parents=True, exist_ok=True)
            sid = _crystal_sid(self.session_id)
            path = d / f"{sid}.json"
            write_json_atomic(path, payload, ensure_ascii=False)
            with self._lock:
                self._persist_rev = rev
            return path
        except Exception:
            return None


_crystals: dict[str, TimeCrystal] = {}
_lock = threading.Lock()


def _crystal_root(home: Path | str | None = None) -> Path:
    if home:
        return Path(home).expanduser()
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir()
    except Exception:
        return default_home()


def _crystal_sid(session_id: str | None) -> str:
    sid = "".join(
        c for c in (session_id or "default") if c.isalnum() or c in "-_"
    )[:48]
    return sid or "default"


def _hydrate_crystal(crystal: TimeCrystal, home: Path | str | None = None) -> None:
    """Load full facts from the sid file on first get (not recent[-10:])."""
    try:
        path = _crystal_root(home) / "time_crystal" / f"{_crystal_sid(crystal.session_id)}.json"
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    facts_raw = data.get("facts")
    if not isinstance(facts_raw, list) or not facts_raw:
        facts_raw = data.get("durable") or data.get("recent") or []
    if not isinstance(facts_raw, list):
        return
    loaded: list[CrystalFact] = []
    for row in facts_raw:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        horizon = str(row.get("horizon") or "session")
        if horizon not in HORIZONS:
            horizon = "session"
        try:
            hits = int(row.get("hits") or 1)
        except (TypeError, ValueError):
            hits = 1
        try:
            ts = float(row.get("ts") or 0) or time.time()
        except (TypeError, ValueError):
            ts = time.time()
        loaded.append(
            CrystalFact(
                text=text,
                horizon=horizon,
                source=str(row.get("source") or ""),
                project_id=str(row.get("project_id") or ""),
                session_id=str(row.get("session_id") or crystal.session_id),
                hits=max(1, hits),
                ts=ts,
            )
        )
    if not loaded:
        return
    with crystal._lock:
        if crystal.facts:
            return
        crystal.facts = loaded[:MAX_CRYSTAL_FACTS]
        with suppress(TypeError, ValueError):
            crystal.promotions = int(data.get("promotions") or 0)
        with suppress(TypeError, ValueError):
            crystal.blocked_secret = int(data.get("blocked_secret") or 0)
        crystal._rev += 1
        crystal._persist_rev = crystal._rev


def get_time_crystal(
    session_id: str | None = None,
    *,
    project_id: str = "",
    home: Path | str | None = None,
) -> TimeCrystal:
    from remedy.core.metabolism.session_registry import registry_get

    key = (session_id or "").strip() or "_default"
    with _lock:
        created = key not in _crystals
        crystal = registry_get(
            _crystals,
            key,
            lambda: TimeCrystal(session_id=key, project_id=project_id),
        )
        if project_id:
            crystal.project_id = project_id
    if created:
        _hydrate_crystal(crystal, home)
    return crystal


def merge_export_durable(*, home: Path | str | None = None) -> list[dict[str, Any]]:
    """Life + live session keys (never the unused ``_export`` crystal)."""
    get_time_crystal("life", home=home)
    if home:
        d = _crystal_root(home) / "time_crystal"
        if d.is_dir():
            for p in d.glob("*.json"):
                stem = p.stem
                if stem and stem not in ("_export", "default"):
                    get_time_crystal(stem, home=home)
    seen: dict[tuple[Any, Any], dict[str, Any]] = {}
    with _lock:
        crystals = list(_crystals.values())
    for crystal in crystals:
        if (crystal.session_id or "") == "_export":
            continue
        for fact in crystal.export_durable():
            seen[(fact.get("text"), fact.get("horizon"))] = fact
    return list(seen.values())


def reset_time_crystal(session_id: str | None = None) -> None:
    key = (session_id or "").strip() or "_default"
    with _lock:
        _crystals.pop(key, None)
