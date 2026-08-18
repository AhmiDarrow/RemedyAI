"""The duplex loop: speculation, backchannels, barge-in.

Frames are driven in by hand rather than in real time — these lock behaviour,
not timing. Timing is measured by the bench (``test_telephony_bench.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from remedy.telephony.backends.fake import comfort_noise, voiced_pcm
from remedy.telephony.line import AudioFrame, Call, CallDirection, CallState
from remedy.telephony.narrowband import frame_bytes
from remedy.voice.realtime.pipeline import (
    PipelineConfig,
    PipelineState,
    VoicePipeline,
    _take_clause,
)
from remedy.voice.realtime.turn import EnergyTurnDetector

FRAME = frame_bytes(8000)


class RecordingCall(Call):
    """A call that keeps everything she said."""

    def __init__(self) -> None:
        super().__init__(remote="+15550100", direction=CallDirection.OUTBOUND)
        self.sent: list[AudioFrame] = []
        self._set_state(CallState.ACTIVE)

    async def send_audio(self, frame: AudioFrame) -> None:
        await super().send_audio(frame)
        self.sent.append(frame)

    @property
    def audible_ms(self) -> float:
        from remedy.telephony.narrowband import rms

        return sum(f.duration_ms for f in self.sent if rms(f.pcm) >= 0.01)


class InstantStt:
    def __init__(self, text: str = "hello there") -> None:
        self.text = text
        self.finals = 0

    def feed(self, pcm: bytes, at: float) -> None:
        return None

    async def final(self) -> str:
        self.finals += 1
        return self.text

    def reset(self) -> None:
        return None


class InstantResponder:
    def __init__(self, text: str = "Yes, that works for us. ") -> None:
        self.text = text
        self.calls: list[str] = []

    async def reply(self, heard: str) -> AsyncIterator[str]:
        self.calls.append(heard)
        yield self.text


class SlowResponder:
    """Never produces anything — used to hold a turn open."""

    async def reply(self, heard: str) -> AsyncIterator[str]:
        await asyncio.sleep(30)
        yield "too late"


class InstantTts:
    sample_rate = 8000

    def __init__(self, ms: int = 400) -> None:
        self.ms = ms
        self.spoken: list[str] = []

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        self.spoken.append(text)
        yield voiced_pcm(self.ms, 8000, f0=200.0)


class FakeClock:
    """Wall time under the test's control.

    The endpointer counts frames, but speculation and backchannels are timed in
    real seconds — deliberately, because those thresholds are latency budgets.
    Feeding frames in a tight loop takes microseconds, so without a controllable
    clock none of that logic would ever be exercised.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _build(call: Call, **kw) -> VoicePipeline:
    kw.setdefault("clock", FakeClock())
    kw.setdefault("stt", InstantStt())
    kw.setdefault("responder", InstantResponder())
    kw.setdefault("tts", InstantTts())
    kw.setdefault("detector", EnergyTurnDetector(hangover_ms=300.0))
    kw.setdefault("config", PipelineConfig(speculate_after_ms=100.0, backchannel_after_ms=200.0))
    kw.setdefault("filler_audio", lambda: voiced_pcm(200, 8000, f0=170.0))
    return VoicePipeline(call=call, **kw)


async def _drive(pipeline: VoicePipeline, pcm: bytes) -> None:
    """Feed PCM as 20 ms frames, advancing the pipeline's clock along with them."""
    clock = pipeline.clock
    for i in range(0, len(pcm), FRAME):
        chunk = pcm[i : i + FRAME].ljust(FRAME, b"\x00")
        await pipeline._on_frame(AudioFrame(pcm=chunk, sample_rate=8000, at=clock()))
        clock.advance(0.02)
        await asyncio.sleep(0)  # let the turn / backchannel tasks make progress


def _quiet(ms: int, seed: int = 0) -> bytes:
    return b"".join(comfort_noise(FRAME, seed=seed + i) for i in range(ms // 20))


@pytest.mark.asyncio
async def test_speculation_starts_before_the_endpointer_is_sure():
    call = RecordingCall()
    p = _build(call)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(160))
    assert p.state is PipelineState.SPECULATING


@pytest.mark.asyncio
async def test_speculative_audio_is_held_until_the_endpoint_confirms():
    """The whole point: the answer is ready early but nobody hears it early."""
    call = RecordingCall()
    p = _build(call, filler_audio=None)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(200))
    await asyncio.sleep(0.05)  # let the turn task get all the way to playout
    assert call.audible_ms == 0
    assert p.state is PipelineState.SPECULATING


@pytest.mark.asyncio
async def test_resumed_speech_discards_the_speculation_silently():
    """Regression: mid-sentence hesitations are shorter than the hangover, so
    the detector never re-fires onset. Without invalidating on resumed audio the
    speculation survives and she answers half a sentence."""
    call = RecordingCall()
    stt = InstantStt()
    responder = InstantResponder()
    p = _build(call, stt=stt, responder=responder, filler_audio=None)

    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(200))  # hesitation -> speculate
    assert p.state is PipelineState.SPECULATING
    await _drive(p, voiced_pcm(400))  # they resume mid-sentence
    assert p.state is PipelineState.LISTENING
    await asyncio.sleep(0.05)
    assert call.audible_ms == 0  # nothing of the discarded answer reached the line
    assert p.metrics.turns == []  # and it is not scored as a turn


@pytest.mark.asyncio
async def test_committed_turn_reaches_the_line():
    call = RecordingCall()
    p = _build(call, filler_audio=None)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(600))
    await asyncio.sleep(0.4)
    assert call.audible_ms > 0
    assert len(p.metrics.turns) == 1
    assert p.metrics.turns[0].her_first_speech > 0


@pytest.mark.asyncio
async def test_backchannel_covers_the_gap_but_is_not_an_answer():
    call = RecordingCall()
    p = _build(call, responder=SlowResponder())
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(400))
    await asyncio.sleep(0.1)
    rec = p.metrics.turns[0]
    assert rec.filler_used
    assert rec.her_first_audio > 0  # she made a sound
    assert rec.her_first_speech == 0  # but she has not answered
    await p.stop()


@pytest.mark.asyncio
async def test_backchannel_over_a_pause_is_not_counted_as_talking_over():
    """A short "mm-hm" during someone's pause is listening, not interrupting."""
    call = RecordingCall()
    p = _build(call, responder=SlowResponder(), far_end_speaking=lambda: True)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(400))
    await asyncio.sleep(0.1)
    assert not p.metrics.turns[0].false_interrupt
    await p.stop()


@pytest.mark.asyncio
async def test_abandoned_backchannel_becomes_a_backchannel_not_a_phantom_turn():
    call = RecordingCall()
    p = _build(call, responder=SlowResponder())
    await _drive(p, voiced_pcm(400))
    # 280 ms: past the backchannel threshold (200) but inside the endpointer's
    # 300 ms hangover, so the turn is still speculative when they resume.
    await _drive(p, _quiet(280))
    await asyncio.sleep(0.1)
    assert p.state is PipelineState.SPECULATING
    await _drive(p, voiced_pcm(400))  # they resume; speculation dies
    assert p.metrics.turns == []
    assert p.metrics.backchannels == 1
    await p.stop()


@pytest.mark.asyncio
async def test_barge_in_stops_her_and_is_recorded():
    call = RecordingCall()
    p = _build(call, tts=InstantTts(ms=4000), filler_audio=None)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(600))
    await asyncio.sleep(0.15)
    assert p.state is PipelineState.SPEAKING
    spoken_before = len(call.sent)

    await _drive(p, voiced_pcm(300))  # they cut in
    assert p.state is PipelineState.LISTENING
    assert len(p.metrics.barge_ins) == 1
    await asyncio.sleep(0.1)
    # She stopped: at most a couple more frames escaped after the interruption.
    assert len(call.sent) - spoken_before < 10


@pytest.mark.asyncio
async def test_empty_transcription_does_not_produce_a_turn():
    call = RecordingCall()
    p = _build(call, stt=InstantStt(text="   "), filler_audio=None)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(600))
    await asyncio.sleep(0.1)
    assert call.audible_ms == 0


@pytest.mark.asyncio
async def test_run_returns_metrics_when_the_call_ends():
    call = RecordingCall()
    p = _build(call)
    call._deliver(AudioFrame(pcm=comfort_noise(FRAME), sample_rate=8000, at=0.0))
    call._set_state(CallState.ENDED)
    metrics = await p.run()
    assert metrics is p.metrics


@pytest.mark.parametrize(
    ("buffer", "expect_clause"),
    [
        ("short", ""),
        # A 5-char clause is not worth a synthesis round-trip, so the comma is
        # skipped and nothing is emitted until more text arrives.
        ("Yes, that works for us. ", "Yes, that works for us. "),
        ("Thanks very much. Next bit", "Thanks very much. "),
        ("Certainly no problem, we can", "Certainly no problem, "),
        ("no punctuation here at all yet", ""),
    ],
)
def test_take_clause_splits_early_enough_to_start_speaking(buffer, expect_clause):
    """Synthesis must start on the first clause, not on the finished sentence."""
    clause, _ = _take_clause(buffer, 12)
    assert clause == expect_clause
