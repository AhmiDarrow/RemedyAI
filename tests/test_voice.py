"""Voice: gender-mapped speak-back + hearing, graceful without engines.

These tests run WITHOUT the [voice] extra installed — they exercise the
mapping, text-cleaning, wav encoding, settings, and the API's fallback
contract (503 + fallback hints), plus engine paths via mocks.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.voice.service import (
    encode_wav,
    load_voice_settings,
    save_voice_settings,
    speakable_text,
    voice_for_gender,
    voice_status,
)


# ---------------------------------------------------------------------------
# Gender → voice (befitting the assigned gender role)
# ---------------------------------------------------------------------------


def test_voice_follows_agent_gender():
    assert voice_for_gender("female") == "af_heart"
    assert voice_for_gender("male") == "am_michael"
    assert voice_for_gender("neutral") == "af_sky"
    # agent_identity aliases resolve too
    assert voice_for_gender("he") == "am_michael"
    assert voice_for_gender("she") == "af_heart"
    # Unknown → default (female is the product default gender)
    assert voice_for_gender("") == "af_heart"
    assert voice_for_gender(None) == "af_heart"


def test_voice_override_wins():
    assert voice_for_gender("male", override="bf_emma") == "bf_emma"
    assert voice_for_gender("female", override="") == "af_heart"


# ---------------------------------------------------------------------------
# Speakable text
# ---------------------------------------------------------------------------


def test_speakable_text_strips_markdown():
    md = (
        "# Done!\n\n"
        "I **added** the [item](https://example.com/x) to your cart.\n\n"
        "```json\n{\"secret\": \"never-read-aloud\"}\n```\n"
        "- total: $64.12\n"
        "Visit https://example.com/track for tracking."
    )
    t = speakable_text(md)
    assert "Done!" in t
    assert "added" in t and "**" not in t
    assert "item" in t and "example.com/x" not in t
    assert "never-read-aloud" not in t
    assert "code shown on screen" in t
    assert "$64.12" in t
    assert "https://" not in t


def test_speakable_text_truncates_on_sentence():
    long = ("This is a sentence. " * 200).strip()
    t = speakable_text(long, max_chars=300)
    assert len(t) <= 300
    assert t.endswith(".")


# ---------------------------------------------------------------------------
# WAV encoding
# ---------------------------------------------------------------------------


def test_encode_wav_roundtrip_plain_list():
    wav_bytes = encode_wav([0.0, 0.5, -0.5, 1.0, -1.0], 24_000)
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 24_000
        assert w.getnframes() == 5


# ---------------------------------------------------------------------------
# Settings (self-contained voice.json under REMEDY_HOME)
# ---------------------------------------------------------------------------


def test_voice_settings_roundtrip(tmp_path: Path):
    cfg = load_voice_settings(tmp_path)
    assert cfg["speak_replies"] is False
    out = save_voice_settings(
        {"speak_replies": True, "speed": 5.0, "stt_model": "base", "junk": "x"},
        tmp_path,
    )
    assert out["speak_replies"] is True
    assert out["speed"] == 2.0  # clamped
    assert out["stt_model"] == "base"
    assert "junk" not in out
    again = load_voice_settings(tmp_path)
    assert again["speak_replies"] is True


# ---------------------------------------------------------------------------
# Status without engines
# ---------------------------------------------------------------------------


def test_status_reports_reasons_when_engines_missing(tmp_path: Path, monkeypatch):
    import remedy.voice.service as svc

    monkeypatch.setattr(svc, "tts_deps_available", lambda: False)
    monkeypatch.setattr(svc, "stt_deps_available", lambda: False)
    st = voice_status(tmp_path, agent_gender="male")
    assert st["tts"]["available"] is False
    assert "remedy-ai[voice]" in st["tts"]["reason"]
    assert st["tts"]["fallback"] == "browser"
    assert st["tts"]["voice"] == "am_michael"
    assert st["stt"]["available"] is False


def test_synthesize_none_without_engine(tmp_path: Path, monkeypatch):
    import remedy.voice.service as svc

    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: None)
    assert svc.synthesize("hello", home_dir=tmp_path) is None


def test_synthesize_with_mock_engine(tmp_path: Path, monkeypatch):
    import remedy.voice.service as svc

    class FakeKokoro:
        def create(self, text, voice="af_heart", speed=1.0):
            assert "screen" not in voice
            # gender male must have resolved to am_michael via config default
            return [0.0, 0.1, -0.1], 24_000

    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: FakeKokoro())
    out = svc.synthesize("**hello** there", gender="male", home_dir=tmp_path)
    assert out is not None
    wav, sr = out
    assert sr == 24_000
    assert wav[:4] == b"RIFF"


# ---------------------------------------------------------------------------
# API contract (no engines installed → graceful fallback, never a 500)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    from remedy.interfaces.api import create_app

    return TestClient(create_app())


def test_api_voice_status_shape(client: TestClient):
    r = client.get("/api/voice/status")
    assert r.status_code == 200
    data = r.json()
    assert "tts" in data and "stt" in data and "settings" in data
    assert isinstance(data["tts"]["voices"], list)


def test_api_speak_falls_back_to_browser_when_unavailable(client: TestClient):
    r = client.post("/api/voice/speak", json={"text": "hello there"})
    if r.status_code == 200:  # [voice] extra + models present on this machine
        assert r.headers["content-type"].startswith("audio/")
    else:
        assert r.status_code == 503
        assert r.json().get("fallback") == "browser"


def test_api_transcribe_rejects_empty_and_degrades(client: TestClient):
    r = client.post(
        "/api/voice/transcribe",
        content=b"",
        headers={"Content-Type": "audio/webm"},
    )
    assert r.status_code == 400
    r2 = client.post(
        "/api/voice/transcribe",
        content=b"not-really-audio",
        headers={"Content-Type": "audio/webm"},
    )
    assert r2.status_code in (200, 503)
    if r2.status_code == 503:
        assert "error" in r2.json()


def test_api_voice_settings_patch(client: TestClient):
    r = client.post("/api/voice/settings", json={"speak_replies": True})
    assert r.status_code == 200
    assert r.json()["speak_replies"] is True
    r2 = client.get("/api/voice/status")
    assert r2.json()["settings"]["speak_replies"] is True


def test_api_speak_respects_disabled(client: TestClient):
    client.post("/api/voice/settings", json={"tts_enabled": False})
    r = client.post("/api/voice/speak", json={"text": "hi"})
    assert r.status_code == 503
    assert r.json().get("fallback") == "browser"
