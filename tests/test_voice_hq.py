"""High-quality voice (Chatterbox) is opt-in, falls back to Kokoro, skips pytest net."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.voice.service import load_voice_settings, save_voice_settings, synthesize


def test_tts_quality_clamps_and_roundtrips(tmp_path: Path):
    cfg = load_voice_settings(tmp_path)
    assert cfg["tts_quality"] == "standard"
    out = save_voice_settings({"tts_quality": "chatterbox"}, tmp_path)
    assert out["tts_quality"] == "hq"
    out2 = save_voice_settings({"tts_quality": "nope"}, tmp_path)
    assert out2["tts_quality"] == "standard"


def test_synthesize_uses_chatterbox_when_hq(tmp_path: Path, monkeypatch):
    import remedy.voice.chatterbox as hq
    import remedy.voice.service as svc

    save_voice_settings({"tts_quality": "hq"}, tmp_path)
    monkeypatch.setattr(hq, "chatterbox_ready", lambda home_dir=None: True)
    monkeypatch.setattr(
        hq, "synthesize", lambda text, gender=None, home_dir=None: (b"RIFFHQ", 24_000)
    )
    monkeypatch.setattr(
        svc, "get_tts_engine", lambda home_dir=None: (_ for _ in ()).throw(
            AssertionError("kokoro must not run when HQ is ready")
        )
    )
    out = synthesize("hello", gender="female", home_dir=tmp_path)
    assert out == (b"RIFFHQ", 24_000)


def test_synthesize_never_installs_chatterbox_inside_a_speak(tmp_path: Path, monkeypatch):
    """HQ on but not ready: fall to Kokoro at once — no pip/download in the request."""
    import remedy.voice.chatterbox as hq
    import remedy.voice.service as svc

    save_voice_settings({"tts_quality": "hq"}, tmp_path)
    monkeypatch.setattr(hq, "chatterbox_ready", lambda home_dir=None: False)
    monkeypatch.setattr(
        hq, "synthesize", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("chatterbox must not be touched before it is ready")
        )
    )
    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: None)
    assert synthesize("hello", gender="female", home_dir=tmp_path) is None


def test_synthesize_hq_exception_falls_back(tmp_path: Path, monkeypatch):
    import remedy.voice.chatterbox as hq
    import remedy.voice.service as svc

    save_voice_settings({"tts_quality": "hq"}, tmp_path)
    monkeypatch.setattr(hq, "chatterbox_ready", lambda home_dir=None: True)
    monkeypatch.setattr(
        hq, "synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: None)
    assert synthesize("hello", gender="female", home_dir=tmp_path) is None


def test_synthesize_falls_back_to_kokoro_if_hq_missing(tmp_path: Path, monkeypatch):
    pytest.importorskip("numpy")
    import remedy.voice.chatterbox as hq
    import remedy.voice.service as svc

    save_voice_settings({"tts_quality": "hq"}, tmp_path)
    monkeypatch.setattr(hq, "synthesize", lambda *a, **k: None)

    class FakeKokoro:
        def create(self, text, voice="af_heart", speed=1.0):
            return [0.0, 0.2], 24_000

    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: FakeKokoro())
    out = synthesize("hello", gender="male", home_dir=tmp_path)
    assert out is not None
    wav, sr = out
    assert wav[:4] == b"RIFF"
    assert sr == 24_000


def test_chatterbox_install_skips_inside_pytest(tmp_path: Path):
    from remedy.voice.chatterbox import chatterbox_install_state, install_chatterbox

    install_chatterbox(tmp_path)
    st = chatterbox_install_state()
    assert st and st.get("status") == "skipped"


def test_hq_status_shape(tmp_path: Path, monkeypatch):
    import remedy.voice.chatterbox as hq

    monkeypatch.setattr(hq, "chatterbox_deps_available", lambda: False)
    monkeypatch.setattr(hq, "chatterbox_installed", lambda home_dir=None: False)
    st = hq.hq_status(tmp_path)
    assert st["available"] is False
    assert st["approx_mb"] == 1100
    assert st["licence"] == "MIT"
    assert st["fallback"] == "kokoro"


def test_every_gender_has_a_human_reference_out_of_the_box(tmp_path: Path):
    """Chatterbox's built-in speaker is not a stable voice: each gender always
    clones a shipped human clip, and an owner's own reference wins for its
    gender only."""
    from remedy.voice.chatterbox import bundled_reference, identity_prompt_path
    from remedy.voice.identity import VoiceIdentity, save, set_reference

    for g in ("female", "male", "neutral"):
        p = identity_prompt_path(g, tmp_path)
        assert p is not None and p == bundled_reference(g)
        assert p.suffix == ".wav" and p.stat().st_size > 100_000
    assert bundled_reference("female") != bundled_reference("male")

    save(VoiceIdentity(gender="male"), tmp_path)
    own = tmp_path / "me.wav"
    own.write_bytes(b"RIFF" + bytes(200))
    set_reference(own, tmp_path)
    assert identity_prompt_path("male", tmp_path) == own
    assert identity_prompt_path("female", tmp_path) == bundled_reference("female")


def test_local_tts_streams_wav_pcm(tmp_path: Path, monkeypatch):
    import asyncio

    from remedy.voice.realtime.tts import LocalTts
    from remedy.voice.service import encode_wav

    wav = encode_wav([0.0, 0.5, -0.5], 24_000)

    monkeypatch.setattr(
        "remedy.voice.service.synthesize",
        lambda text, gender=None, home_dir=None: (wav, 24_000),
    )

    async def collect():
        tts = LocalTts(home_dir=tmp_path, gender="male")
        chunks = [c async for c in tts.stream("hi")]
        return tts.sample_rate, chunks

    sr, chunks = asyncio.run(collect())
    assert sr == 24_000
    from remedy.voice.realtime.tts_stream import frame_size

    assert chunks and len(chunks[0]) == frame_size(24_000)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    from remedy.interfaces.api import create_app

    return TestClient(create_app())


def test_api_status_includes_hq(client: TestClient):
    r = client.get("/api/voice/status")
    assert r.status_code == 200
    data = r.json()
    assert "hq" in data
    assert data["tts"]["quality"] == "standard"
    assert data["settings"]["tts_quality"] == "standard"


def test_api_install_chatterbox_ok(client: TestClient):
    r = client.post("/api/voice/install", json={"component": "chatterbox"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_api_hq_toggle_starts_install(client: TestClient, monkeypatch):
    import remedy.voice.chatterbox as hq

    started: list[str] = []
    monkeypatch.setattr(
        hq, "install_chatterbox_background", lambda home=None: started.append("x") or True
    )
    r = client.post("/api/voice/settings", json={"tts_quality": "hq"})
    assert r.status_code == 200
    assert r.json()["tts_quality"] == "hq"
    assert started == ["x"]
