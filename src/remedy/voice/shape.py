"""Apply her voice identity to whatever an engine produced.

The reference clip decides *who* she is; the identity's numeric traits
decide how she carries it today. Engines come and go (Kokoro, Chatterbox
Nano, whatever is next); this layer reads the same four numbers for all of
them, so the identity — and its slow evolution — survives every swap.

- ``pace``            → time-stretch without changing pitch
- ``pitch_semitones`` → formant-preserving pitch shift (small range)
- ``warmth``          → a gentle spectral tilt (fuller low-mids, softer top)
- ``articulation``    → sampling steadiness for engines that sample

All of it is deliberately modest: the ranges in :mod:`remedy.voice.identity`
keep her the same person across years of nudges.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _floats(samples: Any) -> Any:
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.reshape(-1)
    return arr


def _lowpass_vec(x: Any, sr: int, fc: float) -> Any:
    """Zero-phase one-pole low-pass (forward + backward), so blending it
    with the dry signal tilts the spectrum without phase cancellation."""
    import numpy as np

    a = math.exp(-2.0 * math.pi * fc / float(sr))
    try:
        from scipy.signal import filtfilt

        return filtfilt([1.0 - a], [1.0, -a], x).astype(np.float32)
    except Exception:

        def _one_pass(v: Any) -> Any:
            out = np.empty_like(v)
            acc = 0.0
            for i in range(len(v)):
                acc = a * acc + (1.0 - a) * float(v[i])
                out[i] = acc
            return out

        return _one_pass(_one_pass(x)[::-1])[::-1]


def shape_audio(
    samples: Any,
    sr: int,
    *,
    pace: float = 1.0,
    pitch_semitones: float = 0.0,
    warmth: float = 0.5,
) -> Any:
    """Return *samples* carrying the given traits. Never raises; worst case
    returns the input unchanged."""
    import numpy as np

    x = _floats(samples)
    if len(x) < 64:
        return x
    try:
        if abs(float(pitch_semitones)) >= 0.05:
            import librosa

            x = librosa.effects.pitch_shift(x, sr=sr, n_steps=float(pitch_semitones))
        if abs(float(pace) - 1.0) >= 0.01:
            import librosa

            x = librosa.effects.time_stretch(x, rate=float(pace))
    except Exception as exc:  # noqa: BLE001 — shaping is never worth a silent reply
        logger.info("voice shape: pitch/pace skipped: %s", exc)
    try:
        # Rumble and DC out first: below speech, it only muddies the level.
        x = _highpass_vec(x, sr, 70.0)
        tilt = max(-1.0, min(1.0, (float(warmth) - 0.5) * 2.0))
        if abs(tilt) >= 1e-3:
            # Gentle: acts above ~3 kHz (sheen), never the body of the voice.
            lp = _lowpass_vec(x, sr, 3000.0)
            x = x + (0.25 * tilt) * (lp - x)
    except Exception as exc:  # noqa: BLE001
        logger.info("voice shape: warmth skipped: %s", exc)
    return normalize_level(np.asarray(x, dtype=np.float32))


_TARGET_RMS = 0.1  # -20 dBFS: a normal conversational level for playback
_PEAK_CAP = 0.95


def _highpass_vec(x: Any, sr: int, fc: float) -> Any:
    """Zero-phase one-pole high-pass (x minus its low-pass)."""
    return (x - _lowpass_vec(x, sr, fc)).astype(x.dtype)


def _soft_limit(x: Any, knee: float = 0.8, ceiling: float = _PEAK_CAP) -> Any:
    """Round off only the peaks above *knee*; the body of the signal is untouched.

    A hard gain cap lets one stray peak drop the whole utterance; a limiter
    keeps the level and tames the peak.
    """
    import numpy as np

    a = np.abs(x)
    over = a > knee
    if not np.any(over):
        return x
    span = ceiling - knee
    shaped = knee + span * np.tanh((a[over] - knee) / span)
    out = x.copy()
    out[over] = np.sign(x[over]) * shaped
    return out.astype(np.float32)


def normalize_level(x: Any) -> Any:
    """Bring speech to a steady playback level (engines differ by 10+ dB).

    RMS to about -20 dBFS; gain limited so silence or a whisper is not
    blown up into noise; peaks above the knee are rounded, not clipped.
    """
    import numpy as np

    if len(x) == 0:
        return x
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    if rms < 1e-4:
        return x
    gain = min(6.0, _TARGET_RMS / rms)
    return _soft_limit((x * gain).astype(np.float32))


def sampling_for(articulation: float) -> dict[str, float]:
    """Engine sampling knobs for Chatterbox-class models.

    Lower articulation → steadier, more even delivery; higher → livelier.
    Kept inside the range where the model stays clean.
    """
    a = max(0.0, min(1.0, float(articulation)))
    return {"temperature": round(0.55 + 0.5 * a, 3), "top_p": round(0.85 + 0.1 * a, 3)}


def apply_identity(
    samples: Any, sr: int, home_dir: Path | str | None = None
) -> Any:
    """Shape *samples* by the stored identity (safe default when none)."""
    try:
        from remedy.voice.identity import load

        ident = load(home_dir)
    except Exception:
        return _floats(samples)
    eff = ident.effective()
    return shape_audio(
        samples,
        sr,
        pace=eff["pace"],
        pitch_semitones=eff["pitch_semitones"],
        warmth=eff["warmth"],
    )
