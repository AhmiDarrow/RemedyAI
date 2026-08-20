"""Persistent voice identity — engine-independent clip + bounded evolution."""

from __future__ import annotations

from pathlib import Path

from remedy.voice.identity import evolve, load, reference_wav, save, set_reference


def test_identity_defaults_and_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    ident = load(tmp_path)
    assert ident.gender == "female"
    assert ident.pace == 1.0
    ident.gender = "male"
    ident.pace = 1.4  # clamp
    save(ident, tmp_path)
    again = load(tmp_path)
    assert again.gender == "male"
    assert again.pace == 1.15


def test_evolve_is_bounded_and_journaled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    evolve(tmp_path, pace=0.5, pitch_semitones=9)
    ident = load(tmp_path)
    assert ident.pace == 1.15
    assert ident.pitch_semitones == 2.0
    assert ident.journal and ident.journal[-1]["change"] == "evolve"


def test_reference_wav_uses_saved_clip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    clip = tmp_path / "me.wav"
    clip.write_bytes(b"RIFF" + b"\x00" * 80)
    set_reference(clip, tmp_path)
    assert reference_wav("female", tmp_path) == clip


def test_identity_zero_values_round_trip(tmp_path):
    from remedy.voice.identity import VoiceIdentity, load, save

    save(VoiceIdentity(warmth=0.0, articulation=0.0, pitch_semitones=0.0), tmp_path)
    back = load(tmp_path)
    assert back.warmth == 0.0
    assert back.articulation == 0.0


def test_identity_load_survives_junk_numbers(tmp_path):
    import json

    from remedy.voice.identity import identity_path, load

    p = identity_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"warmth": "warm", "pace": None}), encoding="utf-8")
    ident = load(tmp_path)
    assert ident.warmth == 0.5
    assert ident.pace == 1.0
