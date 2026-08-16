"""Embodiment — Remedy chooses her body on evidence (when there is a choice).

Proprioception gave the organism a sense of how each muscle renders her;
this layer gives that sense somewhere to go. Given the bodies the partner
has configured, `choose_embodiment` ranks them for the *kind of moment* at
hand along two axes at once:

- **capability** — can this body do the work (classify_muscle tier prior,
  blended with valence actually observed with that muscle in episodes), and
- **fidelity** — does this body render her truly (proprioception EMA).

Sometimes the strong body and the true body are different bodies. A build
moment leans capability; a companion moment leans fidelity; plain chat
weighs them evenly. The chooser never pretends one leaderboard fits all
moments.

Single-provider reality (most partners): with one body configured the
chooser is a silent fast-path — no scoring, no narration, zero cost. The
layer still earns its keep there: a low-fidelity only-body automatically
wears denser corrective ballast (see muscle_correction_block), and evidence
keeps accumulating so choice wakes up already informed the day a second
body appears.

Boundaries: advisory, not autonomous. The chooser ranks only candidates the
partner has configured/approved; it never switches mid-build (enforcement
at the caller); Remedy may explain a choice when asked but never narrates
it unprompted. Design record: docs/EMBODIMENT.md.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remedy.memory.soul.proprioception import fidelity_for, muscle_key

# Moment classes → (capability weight, fidelity weight)
MOMENT_WEIGHTS: dict[str, tuple[float, float]] = {
    "build": (0.70, 0.30),
    "chat": (0.50, 0.50),
    "companion": (0.30, 0.70),
}
DEFAULT_MOMENT = "chat"

# Capability priors by classify_muscle tier (0..3)
_TIER_PRIOR = {0: 0.35, 1: 0.50, 2: 0.65, 3: 0.80}
# How much observed episode valence can pull the prior (evidence-capped)
_OBSERVED_MAX_WEIGHT = 0.5

BALLAST_NONE = "none"
BALLAST_LIGHT = "light"
BALLAST_DENSE = "dense"


@dataclass
class EmbodimentReading:
    """One candidate body, scored for this moment."""

    muscle: str
    provider: str
    model: str
    score: float
    capability: float
    fidelity: float
    ballast: str
    solo: bool = False
    reason: str = ""  # for explain-when-asked; never injected unprompted

    def to_public(self) -> dict[str, Any]:
        return {
            "muscle": self.muscle,
            "provider": self.provider,
            "model": self.model,
            "score": round(self.score, 3),
            "capability": round(self.capability, 3),
            "fidelity": round(self.fidelity, 3),
            "ballast": self.ballast,
            "solo": self.solo,
            "reason": self.reason,
        }


def ballast_for(fidelity: float) -> str:
    """How much corrective ballast a body needs to render her truly."""
    f = float(fidelity)
    if f >= 0.72:
        return BALLAST_NONE
    if f >= 0.50:
        return BALLAST_LIGHT
    return BALLAST_DENSE


def _observed_valence(muscle: str, home: str | Path | None = None) -> tuple[float, int]:
    """Mean episode valence (-1..1) and count for one muscle, from the field."""
    with suppress(Exception):
        from remedy.memory.soul.field import load_soul_field

        sf = load_soul_field(home)
        vals = [
            float(e.valence)
            for e in sf.episodes
            if (e.muscle or "").lower() == muscle
        ]
        if vals:
            return sum(vals) / len(vals), len(vals)
    return 0.0, 0


def capability_score(
    provider: str = "",
    model: str = "",
    *,
    base_url: str = "",
    home: str | Path | None = None,
) -> float:
    """Tier prior blended with valence actually observed with this body."""
    prior = 0.55
    with suppress(Exception):
        from remedy.core.muscle_profile import classify_muscle

        prof = classify_muscle(provider, model, base_url=base_url)
        prior = _TIER_PRIOR.get(int(prof.tier), 0.55)
    valence, n = _observed_valence(muscle_key(provider, model), home)
    if n <= 0:
        return prior
    # Evidence-capped blend: observed experience earns up to half the say.
    w = min(_OBSERVED_MAX_WEIGHT, n / 20.0)
    observed = 0.5 + 0.5 * max(-1.0, min(1.0, valence))  # -1..1 → 0..1
    return max(0.05, min(0.99, (1.0 - w) * prior + w * observed))


def read_candidate(
    candidate: dict[str, Any],
    *,
    moment: str = DEFAULT_MOMENT,
    home: str | Path | None = None,
    solo: bool = False,
) -> EmbodimentReading:
    provider = str(candidate.get("provider") or "")
    model = str(candidate.get("model") or "")
    base_url = str(candidate.get("base_url") or "")
    fid = fidelity_for(provider, model, home=home)
    cap = capability_score(provider, model, base_url=base_url, home=home)
    w_cap, w_fid = MOMENT_WEIGHTS.get(moment, MOMENT_WEIGHTS[DEFAULT_MOMENT])
    return EmbodimentReading(
        muscle=muscle_key(provider, model),
        provider=provider,
        model=model,
        score=w_cap * cap + w_fid * fid,
        capability=cap,
        fidelity=fid,
        ballast=ballast_for(fid),
        solo=solo,
    )


def choose_embodiment(
    candidates: list[dict[str, Any]] | None,
    *,
    moment: str = DEFAULT_MOMENT,
    home: str | Path | None = None,
) -> EmbodimentReading | None:
    """Rank configured bodies for this moment; pick the truest fit.

    One candidate → silent fast-path (no scoring narration, `reason` empty).
    Zero candidates → None. Advisory only: the caller owns switching, and
    must never switch mid-build.
    """
    cands = [c for c in (candidates or []) if isinstance(c, dict)]
    if not cands:
        return None
    if len(cands) == 1:
        return read_candidate(cands[0], moment=moment, home=home, solo=True)
    readings = [read_candidate(c, moment=moment, home=home) for c in cands]
    readings.sort(key=lambda r: r.score, reverse=True)
    best, runner = readings[0], readings[1]
    best.reason = (
        f"{moment} moment: {best.muscle} scored {best.score:.2f} "
        f"(capability {best.capability:.2f}, fidelity {best.fidelity:.2f}) "
        f"over {runner.muscle} at {runner.score:.2f}"
    )
    return best


def embodiment_status(
    candidates: list[dict[str, Any]] | None,
    *,
    moment: str = DEFAULT_MOMENT,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Ranked snapshot for status tools / UI. Safe on zero or one body."""
    cands = [c for c in (candidates or []) if isinstance(c, dict)]
    solo = len(cands) == 1
    readings = [
        read_candidate(c, moment=moment, home=home, solo=solo) for c in cands
    ]
    readings.sort(key=lambda r: r.score, reverse=True)
    return {
        "moment": moment if moment in MOMENT_WEIGHTS else DEFAULT_MOMENT,
        "solo": solo,
        "bodies": [r.to_public() for r in readings],
    }
