"""Her voice settles the way a person's does: slowly, with the relationship.

Once a day the identity's *baseline* drifts a small step toward a target
drawn from three things that are already hers:

- how long she has been with this owner and how much they have talked
  (familiarity),
- the speaking style the owner chose (persona: balanced / efficient /
  detailed / playful),
- her own recent stance from the organism vitals (steady / focused /
  strained / playful).

The owner's explicit asks ("a little warmer") live in a separate offset
that drift never erodes. Everything stays inside the identity's clamps,
every step is journaled, and ``revert`` still walks back the asks.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Fraction of the remaining distance covered per daily step: ~weeks to settle.
_STEP = 0.12
# Per-trait caps on how far the baseline may drift from the shipped default.
# Pace and pitch stay where they are: on her engine they cost a phase-vocoder
# pass, which is audible. Drift moves only warmth and articulation.
_REACH = {"pace": 0.0, "pitch_semitones": 0.0, "warmth": 0.12, "articulation": 0.12}


def familiarity(days_together: float, exchanges: int) -> float:
    """0 → just met, 1 → long-settled. Saturates around a few months / thousands of turns."""
    d = max(0.0, float(days_together))
    e = max(0, int(exchanges))
    by_days = 1.0 - math.exp(-d / 45.0)
    by_talk = 1.0 - math.exp(-e / 1500.0)
    return max(0.0, min(1.0, 0.5 * by_days + 0.5 * by_talk))


def target_traits(
    *,
    defaults: dict[str, float],
    familiarity_: float,
    style: str = "",
    stance: str = "",
) -> dict[str, float]:
    """Where the baseline is heading, as a delta from the shipped defaults."""
    f = max(0.0, min(1.0, familiarity_))
    d = {"pace": 0.0, "pitch_semitones": 0.0, "warmth": 0.0, "articulation": 0.0}
    # Familiarity: a little warmer, a touch more unhurried, steadier.
    d["warmth"] += 0.10 * f
    d["pace"] -= 0.02 * f
    d["articulation"] -= 0.04 * f
    s = (style or "").strip().lower()
    if s == "efficient":
        d["pace"] += 0.03
        d["articulation"] += 0.05
    elif s == "detailed":
        d["pace"] -= 0.02
    elif s == "playful":
        d["articulation"] += 0.08
        d["pitch_semitones"] += 0.25
    st = (stance or "").strip().lower()
    if st == "playful":
        d["articulation"] += 0.03
    elif st in ("strained", "frustrated"):
        d["pace"] -= 0.01
        d["articulation"] -= 0.03
    out: dict[str, float] = {}
    for k, base in defaults.items():
        reach = _REACH[k]
        out[k] = base + max(-reach, min(reach, d[k]))
    return out


def drift_once(
    home_dir: Path | str | None,
    *,
    days_together: float,
    exchanges: int,
    style: str = "",
    stance: str = "",
    today: str | None = None,
) -> bool:
    """One daily step of the baseline toward its target. Returns True if moved.

    Idempotent per calendar day (journal records ``drift`` with the date).
    """
    from remedy.voice.identity import VoiceIdentity, load, save

    ident = load(home_dir)
    if ident.held:
        return False  # the owner asked her to keep this voice
    day = today or datetime.now(UTC).strftime("%Y-%m-%d")
    for entry in reversed(ident.journal):
        if entry.get("change") == "drift":
            if entry.get("day") == day:
                return False
            break
    defaults = {
        "pace": VoiceIdentity.pace,
        "pitch_semitones": VoiceIdentity.pitch_semitones,
        "warmth": VoiceIdentity.warmth,
        "articulation": VoiceIdentity.articulation,
    }
    f = familiarity(days_together, exchanges)
    target = target_traits(defaults=defaults, familiarity_=f, style=style, stance=stance)
    moved = False
    before = {k: getattr(ident, k) for k in defaults}
    for k in defaults:
        cur = float(getattr(ident, k))
        nxt = cur + _STEP * (target[k] - cur)
        if abs(nxt - cur) >= 1e-4:
            setattr(ident, k, nxt)
            moved = True
    if not moved:
        return False
    ident.journal.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "change": "drift",
            "day": day,
            "before": before,
            "signals": {
                "familiarity": round(f, 3),
                "days": round(float(days_together), 1),
                "exchanges": int(exchanges),
                "style": style or "",
                "stance": stance or "",
            },
        }
    )
    save(ident, home_dir)
    logger.info("voice: daily drift applied (familiarity %.2f)", f)
    return True


def relationship_signals(home_dir: Path | str | None, memory: Any = None) -> dict[str, Any]:
    """Days together, exchanges, style, stance — from what she already keeps."""
    days = 0.0
    exchanges = 0
    style = ""
    stance = ""
    try:
        if memory is not None:
            import sqlite3

            db = memory._ensure_db()  # noqa: SLF001 — read-only counts on her own store
            row = db.execute(
                "SELECT COUNT(*), MIN(created_at) FROM chat_messages WHERE role = 'user'"
            ).fetchone()
            if row:
                exchanges = int(row[0] or 0)
                first = row[1]
                if first:
                    try:
                        dt = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        days = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400.0)
                    except ValueError:
                        days = 0.0
            del sqlite3
    except Exception as exc:  # noqa: BLE001
        logger.debug("voice drift: memory signals unavailable: %s", exc)
    try:
        from remedy.interfaces.api_support import load_config

        style = str(load_config().get("persona") or "")
    except Exception:
        style = ""
    try:
        from remedy.core.metabolism.organism import load_vitals

        v = load_vitals(home_dir)
        stance = str(v.get("last_stance") or v.get("stance") or v.get("mood") or "")
    except Exception:
        stance = ""
    return {"days_together": days, "exchanges": exchanges, "style": style, "stance": stance}


def daily_drift(home_dir: Path | str | None, memory: Any = None) -> bool:
    """Apply today's step if it has not happened yet. Never raises."""
    try:
        sig = relationship_signals(home_dir, memory)
        return drift_once(
            home_dir,
            days_together=sig["days_together"],
            exchanges=sig["exchanges"],
            style=sig["style"],
            stance=sig["stance"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("voice drift skipped: %s", exc)
        return False
