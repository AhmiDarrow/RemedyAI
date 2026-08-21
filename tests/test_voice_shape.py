"""Her voice identity is audible and bounded, on any engine."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from remedy.voice import identity as vid  # noqa: E402
from remedy.voice.shape import apply_identity, sampling_for, shape_audio  # noqa: E402

SR = 24_000


def tone(seconds: float = 1.0, hz: float = 180.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    # a voiced-ish signal: fundamental plus harmonics
    return (0.5 * np.sin(2 * math.pi * hz * t)
            + 0.25 * np.sin(2 * math.pi * 2 * hz * t)
            + 0.15 * np.sin(2 * math.pi * 6 * hz * t)
            + 0.10 * np.sin(2 * math.pi * 3200.0 * t)).astype(np.float32)


def hf_ratio(x: np.ndarray) -> float:
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return float(spec[freqs > 1800].sum() / (spec.sum() + 1e-9))


def test_defaults_only_clean_and_level_the_audio():
    x = tone() * 0.2  # a quiet engine
    y = shape_audio(x, SR, pace=1.0, pitch_semitones=0.0, warmth=0.5)
    # Same waveform (rumble high-pass aside), steadier level.
    corr = float(np.corrcoef(x[200:-200], y[200:-200])[0, 1])
    assert corr > 0.995
    rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)))
    assert 0.08 <= rms <= 0.12 and float(np.max(np.abs(y))) <= 0.95


def test_level_never_amplifies_silence_or_clips():
    from remedy.voice.shape import normalize_level

    assert np.allclose(normalize_level(np.zeros(1000, dtype=np.float32)), 0.0)
    loud = tone() * 0.99
    assert float(np.max(np.abs(normalize_level(loud)))) <= 0.95
    # One stray spike must not drop the whole utterance: the body keeps its level.
    spiky = tone() * 0.1
    spiky[5000] = 0.99
    out = normalize_level(spiky)
    assert float(np.sqrt(np.mean(out[:4000].astype(np.float64) ** 2))) > 0.07


def test_pace_changes_duration_not_pitch():
    pytest.importorskip("librosa")
    x = tone(2.0)
    slower = shape_audio(x, SR, pace=0.9)
    quicker = shape_audio(x, SR, pace=1.1)
    assert len(shape_audio(x, SR, pace=0.98)) == len(x)  # tiny asks do not stretch
    assert len(slower) > len(x) * 1.05
    assert len(quicker) < len(x) * 0.95


def test_warmth_tilts_the_spectrum_both_ways():
    x = tone()
    neutral = shape_audio(x, SR, warmth=0.5)
    warm = shape_audio(x, SR, warmth=0.8)
    lean = shape_audio(x, SR, warmth=0.2)
    assert hf_ratio(warm) < hf_ratio(neutral) < hf_ratio(lean)
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
    assert lo["temperature"] >= 0.5 and hi["temperature"] <= 1.1


def test_apply_identity_reads_the_stored_identity(tmp_path: Path):
    vid.save(vid.VoiceIdentity(warmth=0.9), tmp_path)
    x = tone()
    y = apply_identity(x, SR, tmp_path)
    assert hf_ratio(y) < hf_ratio(shape_audio(x, SR, warmth=0.5))


# -- evolution -----------------------------------------------------------------


def test_owner_asks_move_the_offset_and_revert_walks_back(tmp_path: Path):
    base = vid.load(tmp_path).effective()
    for _ in range(12):  # well past the clamp
        vid.evolve(tmp_path, warmth=0.06, pace=0.03)
    now = vid.load(tmp_path)
    eff = now.effective()
    assert eff["warmth"] == 1.0 and eff["pace"] == vid._PACE_MAX
    assert now.warmth == pytest.approx(vid.VoiceIdentity.warmth)  # baseline untouched
    vid.revert(tmp_path, steps=100)
    back = vid.load(tmp_path)
    assert back.effective()["warmth"] == pytest.approx(base["warmth"])
    assert back.effective()["pace"] == pytest.approx(base["pace"])
    assert back.journal[-1]["change"] == "revert"


def test_default_identity_is_a_clean_baseline():
    d = vid.VoiceIdentity()
    assert d.pace == 1.0 and d.pitch_semitones == 0.0 and d.warmth == 0.5
    assert sampling_for(d.articulation) == {"temperature": 0.8, "top_p": 0.95}


# -- slow evolution ------------------------------------------------------------


def test_drift_is_daily_bounded_and_respects_the_owner(tmp_path: Path):
    from remedy.voice import evolution as evo

    vid.evolve(tmp_path, warmth=-0.12)  # the owner asked for leaner
    asked = vid.load(tmp_path).effective()["warmth"]
    moved = evo.drift_once(tmp_path, days_together=400, exchanges=5000, style="playful", today="2026-08-21")
    assert moved is True
    again = evo.drift_once(tmp_path, days_together=400, exchanges=5000, style="playful", today="2026-08-21")
    assert again is False  # once per day
    ident = vid.load(tmp_path)
    # Baseline warmed a little; the owner's offset is intact.
    assert ident.warmth > vid.VoiceIdentity.warmth
    assert ident.offset["warmth"] == pytest.approx(-0.12)
    assert ident.effective()["warmth"] > asked
    # Many days: converges inside the reach, never past it.
    for day in range(1, 80):
        evo.drift_once(tmp_path, days_together=400 + day, exchanges=5000, style="playful", today=f"2027-01-{day:02d}")
    final = vid.load(tmp_path)
    assert final.warmth <= vid.VoiceIdentity.warmth + evo._REACH["warmth"] + 1e-6
    assert final.pitch_semitones == vid.VoiceIdentity.pitch_semitones  # drift never moves pitch
    assert final.pace == vid.VoiceIdentity.pace  # nor pace
    assert final.journal[-1]["change"] == "drift" and "signals" in final.journal[-1]


def test_hold_keeps_the_voice_but_owner_asks_still_apply(tmp_path: Path):
    from remedy.voice import evolution as evo

    vid.hold(tmp_path, on=True)
    assert vid.load(tmp_path).held is True
    assert evo.drift_once(tmp_path, days_together=400, exchanges=5000, today="2026-08-21") is False
    before = vid.load(tmp_path).effective()["warmth"]
    vid.evolve(tmp_path, warmth=0.06)  # they asked; that still counts
    assert vid.load(tmp_path).effective()["warmth"] > before
    vid.hold(tmp_path, on=False)
    assert evo.drift_once(tmp_path, days_together=400, exchanges=5000, today="2026-08-21") is True
    changes = [j["change"] for j in vid.load(tmp_path).journal if j["change"] in ("hold", "release")]
    assert changes == ["hold", "release"]


def test_a_new_owner_barely_moves_her():
    from remedy.voice import evolution as evo

    assert evo.familiarity(0, 0) == 0.0
    assert evo.familiarity(1, 5) < 0.05
    assert evo.familiarity(365, 20000) > 0.95


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
    assert set(registry) == {"voice_identity", "voice_adjust", "voice_revert", "voice_hold"}
    before = vid.load(tmp_path).effective()
    out = await registry["voice_adjust"](warmth=2, pace=-1)  # type: ignore[operator]
    assert "Adjusted" in out
    after = vid.load(tmp_path).effective()
    assert after["warmth"] > before["warmth"] and after["pace"] < before["pace"]
    out = await registry["voice_revert"]()  # type: ignore[operator]
    assert "Reverted" in out
    assert vid.load(tmp_path).effective()["warmth"] == pytest.approx(before["warmth"])
    assert "My voice:" in await registry["voice_identity"]()  # type: ignore[operator]
    assert "Kept." in await registry["voice_hold"](keep=True)  # type: ignore[operator]
    assert vid.load(tmp_path).held is True
    assert "Released." in await registry["voice_hold"](keep=False)  # type: ignore[operator]


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
    assert adj["effective"]["warmth"] == pytest.approx(min(1.0, base["effective"]["warmth"] + 0.1))
    rev = c.post("/api/voice/identity/revert", json={"steps": 1}).json()
    assert rev["effective"]["warmth"] == pytest.approx(base["effective"]["warmth"])


@pytest.mark.asyncio
async def test_she_changes_her_voice_when_asked_in_conversation(tmp_path: Path):
    """'A little warmer, please' → she calls voice_adjust, then answers."""
    from remedy.core import turn_context as tc
    from remedy.core.agent import BasicRuntime
    from remedy.core.agent_voice_tools import register_voice_tools
    from remedy.core.react_loop.loop import call_llm_stream
    from remedy.models import AgentConfig
    from tests.harness.fake_llm import FakeLLM, text_turn, tool_turn

    (tmp_path / "proj").mkdir()
    runtime = BasicRuntime(
        AgentConfig(
            name="test",
            home_dir=str(tmp_path / "home"),
            project_path=str(tmp_path / "proj"),
            llm_provider="openai",
            llm_model="fake-model",
            llm_api_key="sk-test",
            llm_base_url="http://llm.invalid/v1",
        ),
        memory=None,
    )
    runtime._max_react_steps = 8
    register_voice_tools(runtime)
    home = tmp_path / "home"
    before = vid.load(home).effective()
    fake = FakeLLM([tool_turn("voice_adjust", {"warmth": 1, "pace": -1}), text_turn("Done — a little warmer and unhurried.")])
    sid = "voice-ask"
    tokens = tc.begin_turn(sid)
    try:
        with fake.patch(force_tools=True):
            chunks = [c async for c in call_llm_stream(runtime, "could you speak a little warmer and slower?", session_id=sid)]
    finally:
        tc.end_turn(None, *tokens)
    after = vid.load(home).effective()
    assert after["warmth"] > before["warmth"] and after["pace"] < before["pace"]
    assert any("Adjusted" in t for t in fake.requests[1].tool_result_texts)
    assert "warmer" in "".join(c for c in chunks if not c.startswith("@@"))
