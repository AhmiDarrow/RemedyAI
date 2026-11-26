"""Targeted tests for the RMDY Python tool worker product surface."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from remedy.runtime import rmdy_tool_worker as worker


def _frame(kind: int, payload: bytes, correlation: bytes | None = None) -> bytes:
    corr = correlation or (b"\x01" + b"\x00" * 15)
    header = b"RMDY" + struct.pack("<HHII", 1, kind, 0, len(payload)) + corr
    return header + payload


def test_voice_vision_handlers_registered() -> None:
    for key in (
        ("voice.speak", 1),
        ("voice.transcribe", 1),
        ("voice.install", 1),
        ("vision.activate", 1),
        ("vision.install", 1),
        ("vision.cancel_install", 1),
        ("vision.reinstall_runtime", 1),
        ("vision.uninstall", 1),
        ("vision.start", 1),
        ("vision.stop", 1),
        ("vision.progress", 1),
    ):
        assert key in worker._HANDLERS


def test_voice_install_unknown_component() -> None:
    from remedy.runtime import voice_vision_rmdy as vv

    out = vv.voice_install({"component": "nope"})
    assert out["ok"] is False
    assert out["started"] is False
    assert "Unknown" in str(out.get("error") or "")


def test_vision_progress_idle() -> None:
    from remedy.runtime import voice_vision_rmdy as vv

    out = vv.vision_progress({})
    assert isinstance(out, dict)
    assert out.get("phase") == "idle"


def test_voice_speak_unavailable_without_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remedy.runtime import voice_vision_rmdy as vv

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))

    def _no_synth(*_a, **_k):
        return None

    monkeypatch.setattr("remedy.voice.service.synthesize", _no_synth)
    out = vv.voice_speak({"text": "hi", "home_dir": str(tmp_path)})
    assert out["unavailable"] is True
    assert out["wav_b64"] == ""
