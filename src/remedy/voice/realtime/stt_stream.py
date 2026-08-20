"""Streaming STT for the duplex pipeline — faster-whisper, local only.

The bench injects an oracle that returns script text. Live calls feed 20 ms
PCM frames here and ask for a final transcript when the turn ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
import wave
from pathlib import Path
from typing import Any

from remedy.telephony.narrowband import PHONE_RATE

logger = logging.getLogger(__name__)


def _write_pcm_wav(raw: bytes, sample_rate: int) -> Path | None:
    """16-bit mono PCM → temp WAV on disk (header only; samples copied as-is)."""
    if len(raw) % 2:
        raw = raw[:-1]
    if not raw:
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        with wave.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sample_rate))
            w.writeframes(raw)
        return Path(f.name)


class WhisperStream:
    """``SttEngine``: buffer frames, transcribe once on ``final()``."""

    def __init__(
        self,
        *,
        home_dir: Any = None,
        sample_rate: int = PHONE_RATE,
    ) -> None:
        self.home_dir = home_dir
        self.sample_rate = int(sample_rate)
        self._chunks: list[bytes] = []

    def feed(self, pcm: bytes, at: float) -> None:
        _ = at
        if pcm:
            self._chunks.append(pcm)

    def reset(self) -> None:
        self._chunks.clear()

    async def final(self) -> str:
        raw = b"".join(self._chunks)
        self._chunks.clear()
        if len(raw) < 640:
            return ""
        from remedy.voice.service import transcribe_file

        tmp: Path | None = None
        try:
            # Everything heavy — the WAV write and whisper — stays off the
            # event loop so the RTP/playout loop keeps its timing.
            tmp = await asyncio.to_thread(_write_pcm_wav, raw, self.sample_rate)
            if tmp is None:
                return ""
            result = await asyncio.to_thread(
                transcribe_file, tmp, home_dir=self.home_dir
            )
        except Exception as exc:
            logger.info("whisper stream failed: %s", exc)
            return ""
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)
        if not result:
            return ""
        return str(result.get("text") or "").strip()
