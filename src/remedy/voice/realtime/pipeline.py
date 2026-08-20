"""The duplex loop — listen, think, speak, and stop the instant you are cut off.

Naive shape of a turn, and why it cannot pass:

    they stop -> wait out the hangover -> STT -> model -> synthesis -> speak

Measured on the bench, that is ``hangover + 590 ms`` of engines. With a 700 ms
hangover she answers in ~1.3 s; even at a reckless 150 ms hangover she is still
at ~750 ms, because the engines alone exceed the 600 ms bar. Shortening the
hangover does not fix it, and it starts cutting people off mid-sentence.

So the loop does three things a naive one does not:

1. **Speculates.** At ``speculate_after_ms`` of silence — long before the
   endpointer is confident — it starts transcribing, thinking, and synthesizing,
   holding the audio back. If the endpoint confirms, the answer is already made.
   If they were only pausing, the work is discarded and nobody hears anything.
2. **Backchannels from the pause, not from the endpoint.** "Mm-hm" fires on a
   timer started when they *stopped making sound*. Overlap here is not a
   mistake: a short backchannel over someone's pause is what listening sounds
   like, which is why it is excluded from the talks-over metric.
3. **Stops mid-frame.** Barge-in is checked between every 20 ms frame.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from remedy.telephony.line import AudioFrame, Call
from remedy.telephony.narrowband import PHONE_RATE, frame_bytes, resample, rms
from remedy.telephony.timing import FramePacer
from remedy.voice.realtime.metrics import CallMetrics, TurnRecord
from remedy.voice.realtime.turn import EnergyTurnDetector, TurnDetector, TurnEvent

logger = logging.getLogger(__name__)

#: Threshold for "she made a sound", used only to timestamp *outbound* audio.
#: Inbound voiced/silent decisions belong to the detector alone — see
#: ``TurnDetector.voiced`` for what happens when two thresholds disagree.
_AUDIBLE = 0.01


class PipelineState(StrEnum):
    LISTENING = "listening"
    #: Working on an answer that has not been committed to yet.
    SPECULATING = "speculating"
    THINKING = "thinking"
    SPEAKING = "speaking"


class SttEngine(Protocol):
    def feed(self, pcm: bytes, at: float) -> None: ...

    async def final(self) -> str: ...

    def reset(self) -> None: ...


class Responder(Protocol):
    def reply(self, heard: str) -> AsyncIterator[str]: ...


class TtsEngine(Protocol):
    sample_rate: int

    def stream(self, text: str) -> AsyncIterator[bytes]: ...


@dataclass(slots=True)
class PipelineConfig:
    sample_rate: int = PHONE_RATE
    #: Silence before we start working on an answer we may never use.
    speculate_after_ms: float = 240.0
    #: Silence before a backchannel covers the gap.
    backchannel_after_ms: float = 400.0
    #: Smallest text run worth synthesizing.
    min_clause_chars: int = 12
    #: Hard cap on one turn's speech, so a runaway model cannot hold the line.
    max_speech_ms: float = 45_000.0


@dataclass
class VoicePipeline:
    """Drives one call. Engines are injected; none of them are imported here."""

    call: Call
    stt: Any
    responder: Any
    tts: Any
    detector: TurnDetector = field(default_factory=EnergyTurnDetector)
    metrics: CallMetrics = field(default_factory=CallMetrics)
    config: PipelineConfig = field(default_factory=PipelineConfig)
    filler_audio: Callable[[], bytes] | None = None
    clock: Callable[[], float] = time.monotonic
    pacer: FramePacer = field(default_factory=FramePacer)
    #: Ground truth for "were they still talking?" (bench only).
    far_end_speaking: Callable[[], bool] | None = None

    state: PipelineState = field(default=PipelineState.LISTENING, init=False)
    _turn: asyncio.Task | None = field(default=None, init=False)
    _backchannel: asyncio.Task | None = field(default=None, init=False)
    _turn_rec: TurnRecord | None = field(default=None, init=False)
    #: Released when the endpointer confirms the turn really ended.
    _commit: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _last_voiced_at: float = field(default=0.0, init=False)
    _last_out_at: float = field(default=0.0, init=False)
    _first_audio_sent: bool = field(default=False, init=False)
    _speaking_started: bool = field(default=False, init=False)
    _spoken_ms: float = field(default=0.0, init=False)
    #: Sub-frame tail of the last synthesis chunk, waiting for the next one.
    _carry: bytearray = field(default_factory=bytearray, init=False)
    #: Has this turn's answer started a paced run on the wire yet?
    _paced_run: bool = field(default=False, init=False)
    _stop: bool = field(default=False, init=False)

    # -- lifecycle -----------------------------------------------------------

    async def run(self) -> CallMetrics:
        """Consume the call until it ends. Returns what it measured."""
        try:
            async for frame in self.call.audio_in():
                if self._stop:
                    break
                await self._on_frame(frame)
        finally:
            await self._abandon_turn()
            # A turn the far end hung up in the middle of is not an answer she
            # withheld, and must not be scored as one. Anything she actually
            # said has already set ``her_first_speech``, which this leaves alone.
            self._drop_turn_record()
        return self.metrics

    async def stop(self) -> None:
        self._stop = True
        await self._abandon_turn()

    async def _abandon_turn(self) -> None:
        for task in (self._backchannel, self._turn):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._turn = self._backchannel = None

    # -- inbound -------------------------------------------------------------

    async def _on_frame(self, frame: AudioFrame) -> None:
        # One clock for everything we measure. Frames carry their own ``at``,
        # but a backend is free to stamp those from a different origin (RTP
        # timestamps, a device clock), and mixing the two silently corrupts
        # every gap we report. ``frame.at`` stays informational.
        now = self.clock()
        self.stt.feed(frame.pcm, now)
        # Feed first, then ask: the detector owns the voiced/silent decision so
        # that this loop and the endpointer can never contradict each other.
        event = self.detector.feed(frame.pcm, now)
        voiced = self.detector.voiced

        if voiced:
            self._last_voiced_at = now
            # They resumed. This cannot wait for a SPEECH_START event: mid-turn
            # hesitations are shorter than the hangover, so the detector never
            # leaves the speaking state and never re-fires onset. Without this,
            # a speculation begun during the pause survives to be committed —
            # and she answers the first half of the sentence.
            if self.state is PipelineState.SPECULATING:
                await self._discard_speculation()

        if event is TurnEvent.SPEECH_START:
            await self._on_speech_start(now)
        elif event is TurnEvent.ENDPOINT:
            self._on_endpoint(now)

        # Speculation and backchannels key off real silence, not the endpointer,
        # because the endpointer is the thing we are trying to get ahead of.
        if self.detector.speaking and not voiced and self._last_voiced_at:
            quiet_ms = (now - self._last_voiced_at) * 1000.0
            if (
                self.state is PipelineState.LISTENING
                and quiet_ms >= self.config.speculate_after_ms
            ):
                self._begin_turn(speculative=True)
            elif (
                self.state is PipelineState.SPECULATING
                and quiet_ms >= self.config.backchannel_after_ms
                and self._backchannel is None
                and not self._first_audio_sent
            ):
                self._backchannel = asyncio.get_running_loop().create_task(
                    self._say_backchannel()
                )

    async def _on_speech_start(self, at: float) -> None:
        if self.state is PipelineState.SPEAKING:
            await self._barge_in(at)
        elif self.state is PipelineState.SPECULATING:
            await self._discard_speculation()

    async def _discard_speculation(self) -> None:
        """They were only drawing breath. Throw the work away silently.

        This is the cost speculation pays and nobody hears it: a backchannel may
        already have gone out, which is fine, but the half-heard answer never
        reaches the line.
        """
        logger.debug("call %s: speculation discarded, they resumed", self.call.id)
        await self._abandon_turn()
        self._drop_turn_record()
        self.state = PipelineState.LISTENING

    def _on_endpoint(self, at: float) -> None:
        if self.state is PipelineState.SPECULATING:
            if self._turn is None or self._turn.done():
                # Nothing to commit to. Belt and braces against the wedge
                # described in ``_respond``: answer from scratch instead.
                self._drop_turn_record()
                self._begin_turn(speculative=False)
                return
            # The gamble paid: release whatever is already synthesized.
            self.state = PipelineState.THINKING
            self._commit.set()
        elif self.state is PipelineState.LISTENING:
            self._begin_turn(speculative=False)

    async def _barge_in(self, at: float) -> None:
        """They cut in. Stop immediately — finishing the sentence is the tell."""
        rec = self.metrics.start_barge_in(at)
        await self._abandon_turn()
        # They took the turn back before she got a word out. That is them
        # interrupting, not her going silent — the barge-in is recorded above
        # and the turn is not scored as unanswered.
        self._drop_turn_record()
        self._carry.clear()
        self.state = PipelineState.LISTENING
        rec.silenced = self.clock()
        logger.debug("barge-in on call %s after %.0f ms", self.call.id, rec.latency_ms)

    # -- the turn ------------------------------------------------------------

    def _begin_turn(self, *, speculative: bool) -> None:
        # The perceived gap starts when they stopped making sound, not when the
        # endpointer's hangover expired — otherwise we would flatter ourselves
        # by exactly the hangover.
        end = self._last_voiced_at or self.clock()
        self._turn_rec = self.metrics.start_turn(end)
        self._last_out_at = end
        self._first_audio_sent = False
        self._speaking_started = False
        self._spoken_ms = 0.0
        self._carry.clear()
        self._paced_run = False
        self._commit = asyncio.Event()
        if self._backchannel is not None and not self._backchannel.done():
            # Recovering from a dead speculation can land here with a filler
            # still playing. Dropping the reference would leave it interleaving
            # frames with the new turn's answer.
            self._backchannel.cancel()
        self._backchannel = None
        if not speculative:
            self._commit.set()
        self.state = (
            PipelineState.SPECULATING if speculative else PipelineState.THINKING
        )
        self._turn = asyncio.get_running_loop().create_task(self._respond())

    def _drop_turn_record(self) -> None:
        """A speculation they talked through is not a turn — do not score it.

        A backchannel may already have gone out over their pause. That is
        listening, not answering, so it is counted separately rather than
        leaving a phantom turn whose "dead air" runs until she next speaks.
        """
        rec = self._turn_rec
        if rec is not None and not rec.her_first_speech and rec in self.metrics.turns:
            if rec.her_first_audio:
                self.metrics.backchannels += 1
            self.metrics.turns.remove(rec)
        self._turn_rec = None

    async def _respond(self) -> None:
        rec = self._turn_rec
        assert rec is not None
        try:
            heard = await self.stt.final()
            self.stt.reset()
            if not heard.strip():
                # Nothing was said, so there is nothing to answer — and nothing
                # to score. A speculative record left behind here would sit in
                # the metrics as a turn she never answered while the real
                # endpoint, arriving moments later, opened a second one.
                self._drop_turn_record()
                self.state = PipelineState.LISTENING
                return

            buffer = ""
            async for chunk in self.responder.reply(heard):
                buffer += chunk
                clause, buffer = _take_clause(buffer, self.config.min_clause_chars)
                if clause:
                    await self._speak(clause, rec)
            if buffer.strip():
                await self._speak(buffer, rec)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("call %s: turn failed", self.call.id)
        finally:
            if self.state is PipelineState.SPECULATING:
                # The speculation died before anyone heard it — an engine error,
                # or a model with nothing to say. Leaving the state here would
                # wedge the call: the next endpoint would commit to a task that
                # no longer exists, and ``_begin_turn`` is only reachable from
                # LISTENING, so she would never speak again.
                self._drop_turn_record()
                self.state = PipelineState.LISTENING
            elif self.state in (PipelineState.SPEAKING, PipelineState.THINKING):
                self.state = PipelineState.LISTENING

    @property
    def _over_speech_budget(self) -> bool:
        return self._spoken_ms >= self.config.max_speech_ms

    async def _speak(self, text: str, rec: TurnRecord) -> None:
        """Synthesize and play, but never before the turn is committed."""
        if self._over_speech_budget:
            return
        # A new synthesis request: its first chunk is warm-up (time-to-first-
        # byte is paid per request), everything after it is owed on the clock.
        self._paced_run = False
        async for pcm in self._prefetched(self.tts.stream(text)):
            await self._commit.wait()
            if self._over_speech_budget:
                # A model that will not stop must not be able to hold a live
                # line open. Cut her off rather than let the far end sit there.
                logger.warning(
                    "call %s: speech budget of %.0f ms exhausted, stopping",
                    self.call.id,
                    self.config.max_speech_ms,
                )
                return
            if not self._speaking_started:
                self._speaking_started = True
                self.state = PipelineState.SPEAKING
            await self._playout(
                pcm, rec, backchannel=False, src_rate=self._tts_rate, flush=False
            )
        if self._carry:
            # End of the clause: nothing is coming to complete the last frame,
            # so pad it out rather than hold audio she has already said.
            await self._playout(
                b"", rec, backchannel=False, src_rate=self._tts_rate, flush=True
            )

    async def _prefetched(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Keep the synthesizer working while the previous chunk plays out.

        Pulling the next chunk only after the current one has finished playing
        serialises synthesis behind playout, and every per-chunk cost — even
        the 40 ms a fast engine takes — lands on the wire as a hole between
        chunks. The per-chunk pacer rebase used to hide exactly that. Run the
        generator ahead into a short queue instead; the queue is bounded so a
        runaway engine cannot pile up minutes of audio she will never say.
        """
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=8)

        async def _produce() -> None:
            try:
                async for pcm in chunks:
                    await queue.put(pcm)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 — re-raised on the consumer side
                await queue.put(exc)
                return
            await queue.put(None)

        producer = asyncio.get_running_loop().create_task(_produce())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer
            aclose = getattr(chunks, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    async def _say_backchannel(self) -> None:
        """The noise a listening human makes. Deliberately not gated on commit."""
        rec = self._turn_rec
        if self.filler_audio is None or rec is None or self._first_audio_sent:
            return
        try:
            rec.filler_used = True
            # Filler is generated at the line rate, not the synthesizer's.
            await self._playout(
                self.filler_audio(),
                rec,
                backchannel=True,
                src_rate=self.config.sample_rate,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Nothing awaits this task, so an unhandled error here vanishes into
            # "Task exception was never retrieved" and the filler silently stops
            # working for the rest of the call.
            logger.exception("call %s: backchannel failed", self.call.id)

    @property
    def _tts_rate(self) -> int:
        return int(getattr(self.tts, "sample_rate", self.config.sample_rate))

    async def _playout(
        self,
        pcm: bytes,
        rec: TurnRecord,
        *,
        backchannel: bool,
        src_rate: int,
        flush: bool = True,
    ) -> None:
        """Send PCM as 20 ms frames, yielding between each so barge-in can land."""
        # One synthesis request is one contiguous run, and it starts at its
        # first chunk. Whatever the engine spent before that — warm-up, a held
        # commit, the gap after a barge-in — is not the transport failing to
        # hold its cadence, and must not be reported as one. But once she is
        # talking, every frame is owed on a 20 ms clock: a synthesizer that
        # stalls between chunks is heard as a stutter, and rebasing per chunk
        # hid exactly that, letting a 700 ms-chunk engine pass the bench with
        # ``late_frames == 0``. A backchannel is its own short run.
        if backchannel or not self._paced_run:
            self.pacer.rebase()
        if not backchannel:
            self._paced_run = True
        # The synthesizer need not speak at the line's rate — Chatterbox runs at
        # 24 kHz — so convert before framing. Framing 24 kHz audio into 8 kHz
        # frames would pace playout at a third of speed, overcount the speech
        # budget threefold, and hand the backend a rate it cannot put on a wire.
        rate = self.config.sample_rate
        if src_rate != rate:
            pcm = resample(pcm, src_rate, rate)
        step = frame_bytes(rate)
        if self._carry:
            pcm = bytes(self._carry) + pcm
            self._carry.clear()
        if not flush:
            # A synthesizer's chunks do not land on 20 ms boundaries. Padding
            # each tail with zeroes splices a click of digital silence into the
            # middle of her sentence at every chunk — up to 20 ms, every time.
            # Carry it into the next chunk instead; ``flush`` pads once, at the
            # end, where the silence is real.
            whole = len(pcm) - (len(pcm) % step)
            self._carry.extend(pcm[whole:])
            pcm = pcm[:whole]
        for off in range(0, len(pcm), step):
            chunk = pcm[off : off + step]
            if len(chunk) < step:
                chunk = chunk + b"\x00" * (step - len(chunk))
            now = self.clock()
            self._spoken_ms += (len(chunk) / 2) / rate * 1000.0
            if rms(chunk) >= _AUDIBLE:
                if not self._first_audio_sent:
                    self._first_audio_sent = True
                    rec.her_first_audio = now
                if not backchannel and not rec.her_first_speech:
                    rec.her_first_speech = now
                    # A backchannel over someone's pause is listening, not
                    # interrupting, so it never counts as talking over them.
                    if not backchannel:
                        rec.false_interrupt = self._far_end_still_talking()
                gap = (now - self._last_out_at) * 1000.0
                if gap > rec.dead_air_ms:
                    rec.dead_air_ms = gap
                self._last_out_at = now
            await self.call.send_audio(AudioFrame(pcm=chunk, sample_rate=rate, at=now))
            # Real-time cadence on absolute deadlines, and the window in which
            # barge-in lands.
            await self.pacer.wait()
            if not backchannel and self._over_speech_budget:
                return

    def _far_end_still_talking(self) -> bool:
        if self.far_end_speaking is not None:
            return bool(self.far_end_speaking())
        return bool(getattr(self.detector, "speaking", False))


def _take_clause(buffer: str, min_chars: int) -> tuple[str, str]:
    """Split off the first speakable clause, so synthesis starts before the
    model has finished thinking. Returns (clause, remainder)."""
    if len(buffer) < min_chars:
        return "", buffer
    for mark in (". ", "? ", "! ", "; ", ", ", " — "):
        idx = buffer.find(mark)
        if idx >= min_chars - 1:
            cut = idx + len(mark)
            return buffer[:cut], buffer[cut:]
    return "", buffer
