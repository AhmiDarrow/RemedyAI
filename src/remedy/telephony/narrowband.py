"""What a phone line does to a voice — G.711 mu-law at 8 kHz, in stdlib.

The bench measures her against the line she will actually speak on, not against
studio audio. A voice that passes at 24 kHz and falls apart through mu-law has
not passed anything. Narrowband cuts both ways: it costs fidelity, and it hides
synthesis artifacts, because everyone on a phone already sounds like a phone.

Pure stdlib on purpose: ``audioop`` was removed in Python 3.13 and the repo
tests on it, so the codec lives here rather than in a shim that vanishes.
"""

from __future__ import annotations

import array
import struct

# G.711 constants (ITU-T / the classic Sun reference implementation)
_BIAS = 0x84
_CLIP = 32635

# Exponent lookup for the top 8 bits of the biased magnitude. Run lengths
# double per segment (2, 2, 4, 8, 16, 32, 64, 128) and sum to 256.
_EXP_LUT = bytes(
    [0] * 2
    + [1] * 2
    + [2] * 4
    + [3] * 8
    + [4] * 16
    + [5] * 32
    + [6] * 64
    + [7] * 128
)

PHONE_RATE = 8000
WIDE_RATE = 16000
FRAME_MS = 20


def frame_samples(sample_rate: int = PHONE_RATE, frame_ms: int = FRAME_MS) -> int:
    """Samples in one frame — 160 at 8 kHz/20 ms, the RTP convention."""
    return int(sample_rate * frame_ms / 1000)


def frame_bytes(sample_rate: int = PHONE_RATE, frame_ms: int = FRAME_MS) -> int:
    """Bytes in one 16-bit mono PCM frame."""
    return frame_samples(sample_rate, frame_ms) * 2


# ---------------------------------------------------------------------------
# G.711 mu-law
# ---------------------------------------------------------------------------


def _linear_to_ulaw_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0x00
    if sign:
        sample = -sample
    if sample > _CLIP:
        sample = _CLIP
    sample += _BIAS
    exponent = _EXP_LUT[(sample >> 7) & 0xFF]
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _ulaw_to_linear_sample(u_val: int) -> int:
    u_val = ~u_val & 0xFF
    t = ((u_val & 0x0F) << 3) + _BIAS
    t <<= (u_val & 0x70) >> 4
    return (_BIAS - t) if (u_val & 0x80) else (t - _BIAS)


_ULAW_DECODE = tuple(_ulaw_to_linear_sample(i) for i in range(256))


def pcm_to_ulaw(pcm: bytes) -> bytes:
    """16-bit LE mono PCM -> 8-bit mu-law (2:1, what the wire carries)."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    return bytes(_linear_to_ulaw_sample(s) for s in samples)


def ulaw_to_pcm(ulaw: bytes) -> bytes:
    """8-bit mu-law -> 16-bit LE mono PCM."""
    out = array.array("h", (_ULAW_DECODE[b] for b in ulaw))
    return out.tobytes()


# ---------------------------------------------------------------------------
# Rate conversion (band-limited enough to be honest, cheap enough for 20 ms)
# ---------------------------------------------------------------------------

# Half-band-ish FIR, normalized. Anti-alias before decimation so the bench does
# not punish a voice for artifacts our own resampler invented.
_LOWPASS = (0.0625, 0.25, 0.375, 0.25, 0.0625)


def _convolve(samples: list[int], taps: tuple[float, ...]) -> list[int]:
    n = len(taps)
    half = n // 2
    padded = [samples[0]] * half + samples + [samples[-1]] * half
    out: list[int] = []
    for i in range(len(samples)):
        acc = 0.0
        for k in range(n):
            acc += padded[i + k] * taps[k]
        out.append(int(max(-32768, min(32767, round(acc)))))
    return out


def _unpack(pcm: bytes) -> list[int]:
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return []
    return list(struct.unpack(f"<{usable // 2}h", pcm[:usable]))


def _pack(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def downsample_2x(pcm: bytes) -> bytes:
    """16 kHz -> 8 kHz, low-passed first."""
    s = _unpack(pcm)
    if not s:
        return b""
    return _pack(_convolve(s, _LOWPASS)[::2])


def upsample_2x(pcm: bytes) -> bytes:
    """8 kHz -> 16 kHz by linear interpolation, then smoothed."""
    s = _unpack(pcm)
    if not s:
        return b""
    stretched: list[int] = []
    for i, cur in enumerate(s):
        nxt = s[i + 1] if i + 1 < len(s) else cur
        stretched.append(cur)
        stretched.append((cur + nxt) // 2)
    return _pack(_convolve(stretched, _LOWPASS))


def to_phone(pcm: bytes, sample_rate: int) -> bytes:
    """Her synthesized audio -> what the far end actually hears (8 kHz PCM)."""
    if sample_rate == WIDE_RATE:
        pcm = downsample_2x(pcm)
    elif sample_rate != PHONE_RATE:
        raise ValueError(f"unsupported source rate {sample_rate}")
    return ulaw_to_pcm(pcm_to_ulaw(pcm))


def from_phone(pcm: bytes, sample_rate: int) -> bytes:
    """Line audio (8 kHz PCM) -> the rate the STT engine wants."""
    if sample_rate == PHONE_RATE:
        return pcm
    if sample_rate == WIDE_RATE:
        return upsample_2x(pcm)
    raise ValueError(f"unsupported target rate {sample_rate}")


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude in [0, 1] — the cheap energy signal."""
    s = _unpack(pcm)
    if not s:
        return 0.0
    acc = sum(float(x) * float(x) for x in s)
    return (acc / len(s)) ** 0.5 / 32768.0
