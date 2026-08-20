"""WhisperStream buffers frames and transcribes once, without a live model."""

from __future__ import annotations

import asyncio
from pathlib import Path

from remedy.voice.realtime.stt_stream import WhisperStream


def test_empty_buffer_is_empty_text():
    s = WhisperStream()

    async def go():
        return await s.final()

    assert asyncio.run(go()) == ""


def test_final_sends_wav_to_transcribe(tmp_path: Path, monkeypatch):
    seen: list[str] = []

    def fake_transcribe(path, language=None, home_dir=None):
        seen.append(str(path))
        data = Path(path).read_bytes()
        assert data[:4] == b"RIFF"
        return {"text": "hello there"}

    monkeypatch.setattr("remedy.voice.service.transcribe_file", fake_transcribe)
    s = WhisperStream(home_dir=tmp_path, sample_rate=8000)
    # 20 ms at 8 kHz = 160 samples = 320 bytes; we need >= 640.
    s.feed(b"\x00\x10" * 400, 0.0)

    async def go():
        return await s.final()

    assert asyncio.run(go()) == "hello there"
    assert seen
    s.reset()
