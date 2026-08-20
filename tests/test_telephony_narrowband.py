"""G.711 and the simulated phone circuit.

``audioop`` was removed in Python 3.13, so the codec is ours and has to be
checked against the standard rather than against the stdlib.
"""

from __future__ import annotations

import math
import struct

import pytest

from remedy.telephony import narrowband as nb


def _pcm(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def test_ulaw_zero_is_255():
    """G.711: linear zero encodes to 0xFF. Getting this wrong inverts silence."""
    assert nb.pcm_to_ulaw(_pcm([0])) == b"\xff"


def test_ulaw_is_byte_per_sample():
    assert len(nb.pcm_to_ulaw(_pcm([0] * 160))) == 160


def test_ulaw_roundtrip_is_logarithmic():
    """mu-law trades absolute accuracy for dynamic range: relative error stays
    small across four orders of magnitude, which a linear codec cannot do."""
    values = [600, -600, 2000, -2000, 8000, -8000, 30000, -30000]
    out = struct.unpack(f"<{len(values)}h", nb.ulaw_to_pcm(nb.pcm_to_ulaw(_pcm(values))))
    for original, decoded in zip(values, out, strict=True):
        assert abs(decoded - original) / abs(original) < 0.07


def test_ulaw_preserves_sign():
    for v in (1000, -1000, 20000, -20000):
        decoded = struct.unpack("<h", nb.ulaw_to_pcm(nb.pcm_to_ulaw(_pcm([v]))))[0]
        assert (decoded > 0) == (v > 0)


def test_ulaw_clips_rather_than_wraps():
    """A full-scale negative sample must not wrap to positive."""
    decoded = struct.unpack("<h", nb.ulaw_to_pcm(nb.pcm_to_ulaw(_pcm([-32768]))))[0]
    assert decoded < 0


def test_frame_sizes_match_rtp_convention():
    assert nb.frame_samples(8000) == 160
    assert nb.frame_bytes(8000) == 320
    assert nb.frame_samples(16000) == 320


def test_downsample_halves_and_upsample_restores_length():
    pcm = _pcm([int(3000 * (i % 7 - 3)) for i in range(320)])
    down = nb.downsample_2x(pcm)
    assert len(down) == len(pcm) // 2
    assert len(nb.upsample_2x(down)) == len(pcm)


def test_to_phone_from_16k_produces_8k_length():
    pcm = _pcm([1000] * 320)  # 20 ms at 16 kHz
    assert len(nb.to_phone(pcm, 16000)) == nb.frame_bytes(8000)


def test_to_phone_from_24k_produces_8k_length():
    """Chatterbox speaks at 24 kHz and the line does not. See resample()."""
    pcm = _pcm([1000] * 480)  # 20 ms at 24 kHz
    assert len(nb.to_phone(pcm, 24000)) == nb.frame_bytes(8000)


def test_to_phone_rejects_nonsense_rate():
    with pytest.raises(ValueError):
        nb.to_phone(_pcm([0] * 100), 0)


def test_resample_is_identity_at_the_same_rate():
    pcm = _pcm([7, -7, 300] * 20)
    assert nb.resample(pcm, 8000, 8000) == pcm


def test_resample_scales_length_by_the_rate_ratio():
    pcm = _pcm([0] * 480)  # 20 ms at 24 kHz
    assert len(nb.resample(pcm, 24000, 8000)) == 160 * 2
    assert len(nb.resample(pcm, 24000, 48000)) == 960 * 2


def test_resample_preserves_a_constant_signal():
    """A DC level must survive the trip, or every voice comes out quieter."""
    out = nb.resample(_pcm([8000] * 480), 24000, 8000)
    samples = struct.unpack(f"<{len(out) // 2}h", out)
    assert all(abs(x - 8000) <= 1 for x in samples)


def test_resample_of_empty_is_empty():
    assert nb.resample(b"", 24000, 8000) == b""


def test_rms_separates_silence_from_speech():
    assert nb.rms(_pcm([0] * 160)) == 0.0
    loud = nb.rms(_pcm([12000, -12000] * 80))
    assert 0.3 < loud < 0.4


def test_rms_of_empty_is_zero():
    assert nb.rms(b"") == 0.0


# ---------------------------------------------------------------------------
# Anti-alias filter before decimation
# ---------------------------------------------------------------------------


def _tone(freq: float, rate: int, ms: int, amp: int = 10000) -> bytes:
    n = rate * ms // 1000
    return _pcm([int(round(amp * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)])


def _middle_rms(pcm: bytes) -> float:
    """RMS of the middle 80% — the edge padding's transient is not the filter."""
    s = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    trim = len(s) // 10
    core = s[trim : len(s) - trim]
    return (sum(x * x for x in core) / len(core)) ** 0.5


def _db(ratio: float) -> float:
    return 20 * math.log10(max(ratio, 1e-12))


@pytest.mark.parametrize(
    ("src_rate", "alias_hz"),
    [(16000, 6000), (24000, 6000), (48000, 6000), (48000, 12000)],
)
def test_decimation_attenuates_content_above_the_target_nyquist(src_rate, alias_hz):
    """A tone above 4 kHz would fold back into the voice band at 8 kHz. The old
    5-tap binomial let a 6 kHz tone through the 24 kHz path nearly intact."""
    tone = _tone(alias_hz, src_rate, 200)
    before = _middle_rms(tone)
    after = _middle_rms(nb.resample(tone, src_rate, nb.PHONE_RATE))
    assert _db(after / before) <= -40


@pytest.mark.parametrize("src_rate", [16000, 24000, 48000])
def test_decimation_passes_the_voice_band_within_1_db(src_rate):
    tone = _tone(1000, src_rate, 200)
    before = _middle_rms(tone)
    after = _middle_rms(nb.resample(tone, src_rate, nb.PHONE_RATE))
    assert abs(_db(after / before)) <= 1.0


@pytest.mark.parametrize("src_rate", [16000, 24000, 48000])
def test_decimation_output_length_matches_the_rate_ratio(src_rate):
    pcm = _pcm([0] * (src_rate * 20 // 1000))  # one 20 ms frame
    assert len(nb.resample(pcm, src_rate, nb.PHONE_RATE)) == nb.frame_bytes(nb.PHONE_RATE)


def test_lowpass_taps_scale_with_the_decimation_factor():
    """The filter is designed for the ratio it is used at, not for 2x only."""
    two, three, six = (len(nb._lowpass_taps(r)) for r in (2.0, 3.0, 6.0))
    assert two < three < six
    for r in (2.0, 3.0, 6.0):
        taps = nb._lowpass_taps(r)
        assert len(taps) % 2 == 1
        assert abs(sum(taps) - 1.0) < 1e-9
