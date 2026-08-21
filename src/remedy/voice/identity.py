"""Her voice identity — persists across engine upgrades.

A reference clip plus numeric prosody, not an engine-specific embedding.
Chatterbox / Kokoro both read this; swapping engines re-uses the same clip
so she still sounds like herself. Evolution is slow and reversible.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_text_atomic

logger = logging.getLogger(__name__)

_PACE_MIN, _PACE_MAX = 0.85, 1.15
_PITCH_MIN, _PITCH_MAX = -2.0, 2.0


def _home(home_dir: Path | str | None = None) -> Path:
    from remedy.voice.service import voice_home

    d = voice_home(home_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def identity_path(home_dir: Path | str | None = None) -> Path:
    return _home(home_dir) / "identity.json"


@dataclass
class VoiceIdentity:
    gender: str = "female"
    reference_wav: str = ""
    # Defaults tuned for a steady, unhurried, even delivery; evolution moves
    # them in small steps inside the clamps below.
    # Baseline: ships neutral-ish and drifts slowly with the relationship
    # (remedy.voice.evolution). Pitch 0 and warmth 0.5 leave the reference
    # clip's own character alone.
    pace: float = 0.97
    pitch_semitones: float = 0.0
    warmth: float = 0.5
    articulation: float = 0.45
    # The owner's explicit asks ("a little warmer"), kept apart from drift
    # so the slow evolution never quietly undoes what they chose.
    offset: dict[str, float] = field(default_factory=dict)
    # "Keep this voice": when set, the slow drift pauses (the owner's own
    # asks still apply, because they asked). Released with hold(on=False).
    held: bool = False
    journal: list[dict[str, Any]] = field(default_factory=list)

    def effective(self) -> dict[str, float]:
        """Baseline + owner offset, clamped — what the engines actually use."""
        vals = {
            "pace": self.pace + float(self.offset.get("pace", 0.0)),
            "pitch_semitones": self.pitch_semitones
            + float(self.offset.get("pitch_semitones", 0.0)),
            "warmth": self.warmth + float(self.offset.get("warmth", 0.0)),
            "articulation": self.articulation + float(self.offset.get("articulation", 0.0)),
        }
        vals["pace"] = min(_PACE_MAX, max(_PACE_MIN, vals["pace"]))
        vals["pitch_semitones"] = min(_PITCH_MAX, max(_PITCH_MIN, vals["pitch_semitones"]))
        vals["warmth"] = min(1.0, max(0.0, vals["warmth"]))
        vals["articulation"] = min(1.0, max(0.0, vals["articulation"]))
        return vals

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["effective"] = self.effective()
        d["has_reference"] = bool(self.reference_wav) and Path(
            self.reference_wav
        ).is_file()
        return d


def _clamp(ident: VoiceIdentity) -> VoiceIdentity:
    ident.pace = min(_PACE_MAX, max(_PACE_MIN, float(ident.pace)))
    ident.pitch_semitones = min(
        _PITCH_MAX, max(_PITCH_MIN, float(ident.pitch_semitones))
    )
    ident.warmth = min(1.0, max(0.0, float(ident.warmth)))
    ident.articulation = min(1.0, max(0.0, float(ident.articulation)))
    g = str(ident.gender or "female").strip().lower()
    ident.gender = g if g in ("female", "male", "neutral") else "female"
    return ident


def load(home_dir: Path | str | None = None) -> VoiceIdentity:
    p = identity_path(home_dir)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return VoiceIdentity()
    if not isinstance(raw, dict):
        return VoiceIdentity()
    ident = VoiceIdentity(
        gender=str(raw.get("gender") or "female"),
        reference_wav=str(raw.get("reference_wav") or ""),
        pace=_num(raw.get("pace"), 0.97),
        pitch_semitones=_num(raw.get("pitch_semitones"), 0.0),
        warmth=_num(raw.get("warmth"), 0.5),
        articulation=_num(raw.get("articulation"), 0.45),
        offset={
            k: _num(v, 0.0)
            for k, v in (raw.get("offset") or {}).items()
            if k in ("pace", "pitch_semitones", "warmth", "articulation")
        }
        if isinstance(raw.get("offset"), dict)
        else {},
        held=bool(raw.get("held", False)),
        journal=list(raw.get("journal") or [])[-40:],
    )
    return _clamp(ident)


def _num(value: Any, default: float) -> float:
    """float or default — 0.0 is a real value, and junk never raises."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def save(ident: VoiceIdentity, home_dir: Path | str | None = None) -> VoiceIdentity:
    ident = _clamp(ident)
    write_text_atomic(
        identity_path(home_dir), json.dumps(ident.public(), indent=2) + "\n"
    )
    return ident


def set_reference(
    wav_path: Path | str, home_dir: Path | str | None = None
) -> VoiceIdentity:
    ident = load(home_dir)
    ident.reference_wav = str(Path(wav_path))
    ident.journal.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "change": "reference",
            "path": ident.reference_wav,
        }
    )
    return save(ident, home_dir)


def evolve(
    home_dir: Path | str | None = None,
    *,
    pace: float | None = None,
    pitch_semitones: float | None = None,
    warmth: float | None = None,
    articulation: float | None = None,
) -> VoiceIdentity:
    """The owner asked for a change: move their offset, not the baseline."""
    ident = load(home_dir)
    before = dict(ident.offset)
    for key, delta in (
        ("pace", pace),
        ("pitch_semitones", pitch_semitones),
        ("warmth", warmth),
        ("articulation", articulation),
    ):
        if delta is not None:
            ident.offset[key] = float(ident.offset.get(key, 0.0)) + float(delta)
    # Keep offsets within what the clamps could ever express.
    ident.offset["pace"] = max(-0.3, min(0.3, ident.offset.get("pace", 0.0)))
    ident.offset["pitch_semitones"] = max(-4.0, min(4.0, ident.offset.get("pitch_semitones", 0.0)))
    ident.offset["warmth"] = max(-1.0, min(1.0, ident.offset.get("warmth", 0.0)))
    ident.offset["articulation"] = max(-1.0, min(1.0, ident.offset.get("articulation", 0.0)))
    ident.journal.append(
        {"at": datetime.now(UTC).isoformat(), "change": "evolve", "before": before}
    )
    return save(ident, home_dir)


def hold(home_dir: Path | str | None = None, *, on: bool = True) -> VoiceIdentity:
    """Freeze (or release) the slow evolution. Owner asks still work."""
    ident = load(home_dir)
    if ident.held != bool(on):
        ident.held = bool(on)
        ident.journal.append(
            {"at": datetime.now(UTC).isoformat(), "change": "hold" if on else "release"}
        )
    return save(ident, home_dir)


def revert(home_dir: Path | str | None = None, *, steps: int = 1) -> VoiceIdentity:
    """Walk back the last *steps* evolutions using their journaled ``before``."""
    ident = load(home_dir)
    n = max(1, int(steps))
    while n > 0:
        idx = next(
            (i for i in range(len(ident.journal) - 1, -1, -1)
             if ident.journal[i].get("change") == "evolve"),
            None,
        )
        if idx is None:
            break
        before = ident.journal.pop(idx).get("before") or {}
        ident.offset = {k: float(v) for k, v in before.items() if k in ("pace", "pitch_semitones", "warmth", "articulation")}
        n -= 1
    ident.journal.append({"at": datetime.now(UTC).isoformat(), "change": "revert"})
    return save(ident, home_dir)


def reference_wav(
    gender: str | None = None, home_dir: Path | str | None = None
) -> Path | None:
    """Engine-independent clip for cloning, if one exists.

    An owned reference is *her* voice and carries the identity's gender; it
    is only used when the requested gender matches (or none was asked), so
    flipping the partner's gender in Settings always changes the voice.
    """
    ident = load(home_dir)
    want = (gender or "").strip().lower()
    if ident.reference_wav and (not want or want == (ident.gender or "").strip().lower()):
        p = Path(ident.reference_wav)
        if p.is_file() and p.stat().st_size > 64:
            return p
    g = (want or ident.gender or "female").strip().lower()
    fallback = _home(home_dir) / "chatterbox" / "identity" / f"{g}.wav"
    if fallback.is_file() and fallback.stat().st_size > 64:
        return fallback
    return None
