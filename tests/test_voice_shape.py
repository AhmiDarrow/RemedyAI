"""Her voice identity is audible and bounded, on any engine."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from remedy.voice import identity as vid  # noqa: E402
from remedy.voice.shape import apply_identity, sampling_for, shape_audio  # noqa: E402

SR = 24_000


def tone(seconds: float = 1.0, hz: float = 180.0) -> "np.ndarray":
    t = np.arange(int(SR * seconds)) / SR
    # a voiced-ish signal: fundamental plus harmonics
    return (0.5 * np.sin(2 * math.pi * hz * t)
            + 0.25 * np.sin(2 * math.pi * 2 * hz * t)
            + 0.15 * np.sin(2 * math.pi * 6 * hz * t)
            + 0.10 * np.sin(2 * math.pi * 3200.0 * t)).astype(np.float32)


def hf_ratio(x: "np.ndarray") -> float:
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return float(spec[freqs > 1800].sum() / (spec.sum() + 1e-9))


def test_defaults_leave_audio_untouched():
    x = tone()
    y = shape_audio(x, SR, pace=1.0, pitch_semitones=0.0, warmth=0.5)
    assert np.allclose(x, y)


def test_pace_changes_duration_not_pitch():
    pytest.importorskip("librosa")
    x = tone(2.0)
    slower = shape_audio(x, SR, pace=0.9)
    quicker = shape_audio(x, SR, pace=1.1)
    assert len(slower) > len(x) * 1.05
    assert len(quicker) < len(x) * 0.95


def test_warmth_tilts_the_spectrum_both_ways():
    x = tone()
    warm = shape_audio(x, SR, warmth=0.8)
    lean = shape_audio(x, SR, warmth=0.2)
    assert hf_ratio(warm) < hf_ratio(x) < hf_ratio(lean)
    assert float(np.max(np.abs(warm))) <= 0.98


def test_pitch_shift_moves_the_fundamental():
    librosa = pytest.importorskip("librosa")
    x = tone(1.5, 180.0)
    up = shape_audio(x, SR, pitch_semitones=2.0)
    f0_x, vx, _ = librosa.pyin(x, fmin=80, fmax=400, sr=SR)
    f0_u, vu, _ = librosa.pyin(up, fmin=80, fmax=400, sr=SR)
    assert np.nanmedian(f0_u[vu]) > np.nanmedian(f0_x[vx]) * 1.08


def test_shaping_never_raises_on_junk():
    assert len(shape_audio([], SR)) == 0
    assert len(shape_audio([0.0] * 10, SR, pace=0.9, warmth=0.9)) == 10


def test_sampling_is_steadier_at_low_articulation():
    lo, hi = sampling_for(0.0), sampling_for(1.0)
    assert lo["temperature"] < hi["temperature"]
    assert 0.5 <= lo["temperature"] and hi["temperature"] <= 1.1


def test_apply_identity_reads_the_stored_identity(tmp_path: Path):
    vid.save(vid.VoiceIdentity(warmth=0.9), tmp_path)
    x = tone()
    y = apply_identity(x, SR, tmp_path)
    assert hf_ratio(y) < hf_ratio(x)


# -- evolution -----------------------------------------------------------------


def test_evolution_is_small_bounded_and_reversible(tmp_path: Path):
    base = vid.load(tmp_path)
    for _ in range(12):  # well past the clamp, within the journal's memory
        vid.evolve(tmp_path, warmth=0.06, pace=0.03)
    now = vid.load(tmp_path)
    assert now.warmth == 1.0 and now.pace == vid._PACE_MAX
    # Reverts walk the journal back to the start.
    vid.revert(tmp_path, steps=100)
    back = vid.load(tmp_path)
    assert back.warmth == pytest.approx(base.warmth)
    assert back.pace == pytest.approx(base.pace)
    assert back.journal[-1]["change"] == "revert"


def test_default_identity_is_unhurried_and_even():
    d = vid.VoiceIdentity()
    assert d.pace < 1.0
    assert d.warmth > 0.5
    assert d.articulation < 0.5


# -- tools --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_tools_adjust_and_revert(tmp_path: Path):
    from remedy.core.agent_voice_tools import register_voice_tools

    registry: dict[str, object] = {}

    class _Reg:
        def register_builtin_handler(self, name, desc, fn, params):
            registry[name] = fn

    class _Runtime:
        tool_registry = _Reg()
        config = {"home_dir": str(tmp_path)}

    register_voice_tools(_Runtime())
    assert set(registry) == {"voice_identity", "voice_adjust", "voice_revert"}
    before = vid.load(tmp_path)
    out = await registry["voice_adjust"](warmth=2, pace=-1)  # type: ignore[operator]
    assert "Adjusted" in out
    after = vid.load(tmp_path)
    assert after.warmth > before.warmth and after.pace < before.pace
    out = await registry["voice_revert"]()  # type: ignore[operator]
    assert "Reverted" in out
    assert vid.load(tmp_path).warmth == pytest.approx(before.warmth)
    assert "My voice:" in await registry["voice_identity"]()  # type: ignore[operator]


def test_identity_routes(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    monkeypatch.setenv("REMEDY_NO_FIRST_RUN_DOWNLOAD", "1")
    from remedy.interfaces.api import create_app

    c = TestClient(create_app())
    base = c.get("/api/voice/identity").json()
    assert {"pace", "pitch_semitones", "warmth", "articulation"} <= set(base)
    adj = c.post("/api/voice/identity/adjust", json={"warmth": 0.1}).json()
    assert adj["warmth"] == pytest.approx(min(1.0, base["warmth"] + 0.1))
    rev = c.post("/api/voice/identity/revert", json={"steps": 1}).json()
    assert rev["warmth"] == pytest.approx(base["warmth"])
