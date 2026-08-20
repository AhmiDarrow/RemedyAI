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
    pace: float = 1.0
    pitch_semitones: float = 0.0
    warmth: float = 0.5
    articulation: float = 0.5
    journal: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
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
        pace=_num(raw.get("pace"), 1.0),
        pitch_semitones=_num(raw.get("pitch_semitones"), 0.0),
        warmth=_num(raw.get("warmth"), 0.5),
        articulation=_num(raw.get("articulation"), 0.5),
        journal=list(raw.get("journal") or [])[-20:],
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
    ident = load(home_dir)
    before = ident.public()
    if pace is not None:
        ident.pace = ident.pace + float(pace)
    if pitch_semitones is not None:
        ident.pitch_semitones = ident.pitch_semitones + float(pitch_semitones)
    if warmth is not None:
        ident.warmth = ident.warmth + float(warmth)
    if articulation is not None:
        ident.articulation = ident.articulation + float(articulation)
    ident = _clamp(ident)
    ident.journal.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "change": "evolve",
            "before": {
                k: before[k]
                for k in ("pace", "pitch_semitones", "warmth", "articulation")
            },
        }
    )
    return save(ident, home_dir)


def reference_wav(
    gender: str | None = None, home_dir: Path | str | None = None
) -> Path | None:
    """Engine-independent clip for cloning, if one exists."""
    ident = load(home_dir)
    if ident.reference_wav:
        p = Path(ident.reference_wav)
        if p.is_file() and p.stat().st_size > 64:
            return p
    g = (gender or ident.gender or "female").strip().lower()
    fallback = _home(home_dir) / "chatterbox" / "identity" / f"{g}.wav"
    if fallback.is_file() and fallback.stat().st_size > 64:
        return fallback
    return None
