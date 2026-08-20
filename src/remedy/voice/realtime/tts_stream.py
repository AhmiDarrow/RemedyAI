"""Chop synthesized PCM into 20 ms frames so playout can start a run.

Kokoro and Chatterbox still finish a clause before the first byte exists.
Once the bytes are here, yielding them a frame at a time keeps the pacer
honest instead of handing it a ten-second blob as one "chunk".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from remedy.telephony.narrowband import FRAME_MS


def frame_size(sample_rate: int) -> int:
    """Bytes in one 20 ms s16le mono frame at *sample_rate*."""
    return max(2, int(sample_rate * FRAME_MS / 1000) * 2)


def iter_frames(pcm: bytes, sample_rate: int) -> Iterator[bytes]:
    step = frame_size(sample_rate)
    if not pcm:
        return
    for i in range(0, len(pcm), step):
        chunk = pcm[i : i + step]
        if len(chunk) < step:
            chunk = chunk + b"\x00" * (step - len(chunk))
        yield chunk


async def stream_frames(
    pcm: bytes, sample_rate: int
) -> AsyncIterator[bytes]:
    for chunk in iter_frames(pcm, sample_rate):
        yield chunk
