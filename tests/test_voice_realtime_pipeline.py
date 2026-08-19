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


class FlakyResponder:
    """Fails once, then behaves. Stands in for a transient LLM/TTS error."""

    def __init__(self, text: str = "Yes, that works for us. ") -> None:
        self.text = text
        self.calls: list[str] = []

    async def reply(self, heard: str) -> AsyncIterator[str]:
        self.calls.append(heard)
        if len(self.calls) == 1:
            raise RuntimeError("engine hiccup")
        yield self.text


class SilentResponder:
    """Answers nothing at all — a real possibility, and not an error."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def reply(self, heard: str) -> AsyncIterator[str]:
        self.calls.append(heard)
        return
        yield ""  # pragma: no cover — makes this an async generator


class WidebandTts:
    """A synthesizer that does not speak at the line's rate. Chatterbox is 24k."""

    sample_rate = 24000

    def __init__(self, ms: int = 300) -> None:
        self.ms = ms

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        yield voiced_pcm(self.ms, self.sample_rate, f0=200.0)


async def _say_something(p: VoicePipeline) -> None:
    """One complete utterance: speech, a hesitation, then a real endpoint."""
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(600))
    await asyncio.sleep(0.05)


class BrokenResponder:
    """Always fails. A dead engine must not also be a dead call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def reply(self, heard: str) -> AsyncIterator[str]:
        self.calls.append(heard)
        raise RuntimeError("engine down")
        yield ""  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_a_failed_speculation_does_not_wedge_the_call():
    """One transient engine error used to cost every remaining turn.

    The turn task died while the state was still SPECULATING, so the next
    endpoint committed to a task that no longer existed, and ``_begin_turn`` is
    only reachable from LISTENING — the pipeline sat in THINKING for the rest of
    the call and never spoke again.
    """
    call = RecordingCall()
    responder = BrokenResponder()
    p = _build(call, responder=responder, filler_audio=None)

    for _ in range(3):
        await _say_something(p)
        assert p.state is not PipelineState.THINKING

    # Still taking turns rather than sitting on a task that died three
    # utterances ago.
    assert len(responder.calls) >= 3
    assert p.state is PipelineState.LISTENING


@pytest.mark.asyncio
async def test_she_speaks_again_after_a_transient_engine_error():
    call = RecordingCall()
    responder = FlakyResponder()
    p = _build(call, responder=responder, filler_audio=None)
    await _say_something(p)
    await _say_something(p)
    assert len(responder.calls) >= 2
    assert call.audible_ms > 0


@pytest.mark.asyncio
async def test_a_speculation_with_nothing_to_say_returns_to_listening():
    call = RecordingCall()
    responder = SilentResponder()
    p = _build(call, responder=responder, filler_audio=None)
    await _say_something(p)
    assert p.state is PipelineState.LISTENING


@pytest.mark.asyncio
async def test_wideband_synthesis_reaches_the_line_at_the_line_rate():
    """Framing 24 kHz audio into 8 kHz frames paced playout at a third speed,
    tripled the speech budget, and handed the backend a rate it cannot send."""
    call = RecordingCall()
    p = _build(call, tts=WidebandTts(300), filler_audio=None)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(600))
    await asyncio.sleep(0.5)  # real time: playout paces 20 ms per frame

    assert call.sent, "she never spoke"
    assert {f.sample_rate for f in call.sent} == {8000}
    assert all(len(f.pcm) == FRAME for f in call.sent)
    # 300 ms of synthesis is 300 ms on the wire, not 900.
    assert 280 <= call.audible_ms <= 320
    assert 280 <= p._spoken_ms <= 320


class RaggedTts:
    """Chunks that do not land on a 20 ms boundary — i.e. any real synthesizer."""

    sample_rate = 8000

    def __init__(self, chunk_ms: float = 25.0, chunks: int = 6) -> None:
        self.chunk_ms = chunk_ms
        self.chunks = chunks

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        samples = int(self.sample_rate * self.chunk_ms / 1000)
        for i in range(self.chunks):
            yield voiced_pcm(1000, self.sample_rate, f0=200.0, seed=i)[: samples * 2]


@pytest.mark.asyncio
async def test_ragged_synthesis_chunks_do_not_splice_in_silence():
    """Padding every chunk's tail put a click of digital silence in the middle
    of her sentence — up to 20 ms, at every chunk a real synthesizer emits."""
    call = RecordingCall()
    tts = RaggedTts(chunk_ms=25.0, chunks=6)
    p = _build(call, tts=tts, filler_audio=None)
    await _drive(p, voiced_pcm(400))
    await _drive(p, _quiet(600))
    await asyncio.sleep(0.5)

    assert call.sent, "she never spoke"
    # 6 x 25 ms = 150 ms of speech: 7 full frames plus one padded tail.
    assert len(call.sent) == 8
    quiet = [f for f in call.sent if f.pcm.count(b"\x00\x00") > 40]
    assert len(quiet) <= 1, "silence spliced into the middle of her sentence"
    assert 140 <= p._spoken_ms <= 170


class ExplodingFiller:
    """A filler generator that fails. Nothing awaits the task it runs in."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        raise RuntimeError("no filler audio")


@pytest.mark.asyncio
async def test_a_failing_backchannel_does_not_sink_the_turn(caplog):
    """Nothing awaits the backchannel task, so an unhandled error there used to
    vanish into "Task exception was never retrieved"."""
    call = RecordingCall()
    filler = ExplodingFiller()
    p = _build(call, filler_audio=filler)
    await _say_something(p)
    await asyncio.sleep(0.1)

    assert filler.calls >= 1, "the backchannel never fired"
    assert p.state is not PipelineState.THINKING
    assert call.audible_ms > 0, "the answer never went out"
