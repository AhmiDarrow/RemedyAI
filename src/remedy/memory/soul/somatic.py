"""Somatic signals — organism mood from Soul Field (bond, stance, residue).

Not medical psychometrics: a local UI/tray signal so the living partner is
visible on the machine (status bar + tray tooltip).
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.memory.soul.field import load_soul_field, soul_dir


@dataclass
class SomaSnapshot:
    """Public mood packet for API / tray / status bar."""

    mood: str  # calm | focused | strained | playful | recovering | dormant
    emoji: str
    label: str
    rapport: float
    trust: float
    last_stance: str
    open_threads: int
    episodes: int
    muscle_hint: str
    tray_tooltip: str
    ts: float

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def _mood_from_field(
    *,
    rapport: float,
    trust: float,
    last_stance: str,
    turns: int,
    open_threads: int,
) -> tuple[str, str, str]:
    """Return (mood_id, emoji, short_label)."""
    stance = (last_stance or "steady").lower()
    if turns <= 0 and open_threads == 0:
        return "dormant", "◐", "Resting"
    if stance == "frustrated" or trust < 0.4:
        return "strained", "◎", "Strained — fix first"
    if stance == "playful" and rapport >= 0.55:
        return "playful", "✦", "Playful"
    if stance == "focused" or open_threads >= 2:
        return "focused", "◆", "Focused"
    if trust < 0.5 or rapport < 0.45:
        return "recovering", "◇", "Recovering bond"
    return "calm", "●", "Calm"


def compute_soma(
    home: str | Path | None = None,
    *,
    muscle_label: str = "",
    muscle_provider: str = "",
    field: Any = None,
) -> SomaSnapshot:
    """Compute somatic snapshot from current Soul Field."""
    sf = field if field is not None else load_soul_field(home)
    rel = sf.relational
    last_stance = "steady"
    if sf.episodes:
        last_stance = sf.episodes[-1].user_stance or "steady"
    life_title = ""
    life_open = 0
    with suppress(Exception):
        from remedy.core.metabolism.organism import load_vitals

        v = load_vitals(home)
        if v.get("life_title") or v.get("open_count"):
            life_title = str(v.get("life_title") or "")
            life_open = int(v.get("open_count") or 0)
    if not life_title and not life_open:
        with suppress(Exception):
            from remedy.memory.life_goals import LifeGoalStore

            store = LifeGoalStore(home)
            life_open = store.open_count()
            ag = store.active()
            if ag is not None:
                life_title = ag.title
    if life_open and last_stance == "steady":
        last_stance = "focused"
    mood, emoji, label = _mood_from_field(
        rapport=float(rel.rapport),
        trust=float(rel.trust),
        last_stance=last_stance,
        turns=int(rel.turns_together) + (1 if life_open else 0),
        open_threads=len(rel.open_threads) + life_open,
    )
    muscle_hint = ""
    if muscle_label or muscle_provider:
        muscle_hint = f"{muscle_label or 'muscle'}"
        if muscle_provider:
            muscle_hint += f" · {muscle_provider}"
    thread_bit = ""
    if life_title:
        thread_bit = f" · {life_title[:40]}"
    elif rel.open_threads:
        thread_bit = f" · {rel.open_threads[-1][:40]}"
    who = (getattr(sf, "identity_name", "") or "Remedy").strip() or "Remedy"
    tooltip = (
        f"{who} {emoji} {label} · rapport {rel.rapport:.0%} · trust {rel.trust:.0%}"
        f"{(' · ' + muscle_hint) if muscle_hint else ''}"
        f"{thread_bit}"
    )
    if len(tooltip) > 120:
        tooltip = tooltip[:117] + "…"
    return SomaSnapshot(
        mood=mood,
        emoji=emoji,
        label=label,
        rapport=round(float(rel.rapport), 3),
        trust=round(float(rel.trust), 3),
        last_stance=last_stance,
        open_threads=len(rel.open_threads),
        episodes=len(sf.episodes),
        muscle_hint=muscle_hint,
        tray_tooltip=tooltip,
        ts=time.time(),
    )


def persist_soma(snap: SomaSnapshot, home: str | Path | None = None) -> Path:
    """Write soma.json for desktop/tray consumers."""
    path = soul_dir(home) / "soma.json"
    write_json_atomic(path, snap.to_public(), ensure_ascii=False)
    return path


def load_soma_file(home: str | Path | None = None) -> dict[str, Any] | None:
    path = soul_dir(home) / "soma.json"
    if not path.is_file():
        return None
    with suppress(Exception):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    return None


def refresh_soma(
    home: str | Path | None = None,
    *,
    muscle_label: str = "",
    muscle_provider: str = "",
) -> dict[str, Any]:
    """Compute, persist, return public dict."""
    snap = compute_soma(
        home, muscle_label=muscle_label, muscle_provider=muscle_provider
    )
    with suppress(Exception):
        persist_soma(snap, home)
    return snap.to_public()
