"""Product TTS for the duplex pipeline — Kokoro or Chatterbox HQ.

The bench injects a tone synthesizer. Live calls (and Grove, via
``voice.service.synthesize``) use this wrapper so the pipeline never imports
an engine by name.
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncIterator
from typing import Any


class LocalTts:
    """``TtsEngine`` that speaks through the same path Grove uses."""

    def __init__(
        self,
        *,
        home_dir: Any = None,
        gender: str | None = "female",
    ) -> None:
        self.home_dir = home_dir
        self.gender = gender
        self.sample_rate = 24_000

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        from remedy.telephony.checkpoints import may_speak
        from remedy.voice.service import synthesize

        ok, refusal = may_speak(text)
        spoken = text if ok else (refusal or "")
        if not spoken:
            return
        out = await asyncio.to_thread(
            synthesize,
            spoken,
            gender=self.gender,
            home_dir=self.home_dir,
        )
        if out is None:
            return
        wav, sr = out
        self.sample_rate = int(sr)
        pcm = _wav_pcm(wav)
        if not pcm:
            return
        from remedy.voice.realtime.tts_stream import iter_frames

        for chunk in iter_frames(pcm, self.sample_rate):
            yield chunk


def _wav_pcm(wav: bytes) -> bytes:
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.readframes(w.getnframes())
