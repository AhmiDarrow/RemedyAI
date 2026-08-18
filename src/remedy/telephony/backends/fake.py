"""The bench line — a scripted counterpart on a simulated phone circuit.

Phase 0 exists so the voice is proven before any hardware is bought. This
backend gives the pipeline a far end that behaves like a person: it takes its
turn when she stops, it interrupts mid-sentence, it goes quiet at awkward
moments, and everything she says comes back through mu-law at 8 kHz.

Audio here is synthetic but real PCM — voiced buzz with syllable structure, not
a flag — so energy VADs, endpointers, and the barge-in detector are exercised
for real rather than mocked past.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import struct
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any

from remedy.telephony.line import (
    AudioFrame,
    Call,
    CallDirection,
    CallState,
    Capabilities,
    EndReason,
)
from remedy.telephony.narrowband import (
    FRAME_MS,
    PHONE_RATE,
    frame_samples,
    to_phone,
)
from remedy.telephony.timing import FramePacer

logger = logging.getLogger(__name__)

#: Audio shorter than this from her end reads as a backchannel, not a turn.
MIN_TURN_MS = 500.0

#: A gap this long from her ends one run of speech and begins another. A
#: backchannel is therefore its own run, and the answer that follows it starts a
#: fresh one — which is what makes "interrupt her 500 ms in" mean 500 ms into
#: what she is *saying*, not 500 ms after she went "mm-hm".
SPEECH_RUN_GAP_MS = 250.0


class Cue(StrEnum):
    """When the counterpart takes this line."""

    #: Normal turn-taking: wait for her to finish, then a human beat.
    AFTER_HER_TURN = "after_her_turn"
    #: Cut across her while she is still speaking — the barge-in case.
    INTERRUPT = "interrupt"
    #: Fixed offset from the moment the call goes active (IVR prompts).
    AT_TIME = "at_time"


@dataclass(slots=True)
class Utterance:
    """One thing the far end says."""

    text: str
    duration_ms: int = 0
    cue: Cue = Cue.AFTER_HER_TURN
    #: AFTER_HER_TURN: pause before replying. INTERRUPT: ms into her speech.
    #: AT_TIME: ms after the call went active.
    offset_ms: int = 350
    #: Silence after this line before the next cue is evaluated.
    tail_ms: int = 0
    #: A mid-sentence hesitation: (ms into the utterance, ms of silence).
    #: This is what makes a short endpointer hangover dangerous — "so what I'd
    #: like to ask about is ... Tuesday" is one turn, not two.
    hesitate_at_ms: int = 0
    hesitate_ms: int = 0

    def __post_init__(self) -> None:
        if self.duration_ms <= 0:
            # ~180 ms/word is ordinary conversational pace.
            words = max(1, len(self.text.split()))
            self.duration_ms = int(words * 180)


@dataclass(slots=True)
class SpokenSpan:
    """Ground truth: what was said on the line, and exactly when."""

    text: str
    start: float
    end: float = 0.0
    by_far_end: bool = True


@lru_cache(maxsize=256)
def voiced_pcm(
    duration_ms: int,
    sample_rate: int = PHONE_RATE,
    *,
    f0: float = 118.0,
    amplitude: float = 0.32,
    seed: int = 0,
) -> bytes:
    """Speech-shaped buzz: harmonics, syllable envelope, brief inter-syllable dips.

    Not intelligible, but it has the energy contour real endpointers key on, so
    a VAD tuned against this is not tuned against a square wave.

    Cached because it is not cheap: three seconds of audio costs ~20 ms of pure
    Python, and generating it inline blocked the event loop for a full frame at
    exactly the moment the far end started speaking — distorting the very
    timings this harness exists to measure. The result is immutable bytes and
    the inputs are few, so caching is free correctness.
    """
    n = int(sample_rate * duration_ms / 1000)
    out: list[int] = []
    syllable = 0.22  # seconds
    for i in range(n):
        t = i / sample_rate
        # Syllable envelope: raised cosine with a floor, dipping between beats.
        phase = (t % syllable) / syllable
        env = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * phase))
        # A drifting f0 keeps it from sounding like a test tone.
        f = f0 * (1.0 + 0.04 * math.sin(2 * math.pi * 0.7 * t + seed))
        v = (
            math.sin(2 * math.pi * f * t)
            + 0.5 * math.sin(2 * math.pi * 2 * f * t)
            + 0.25 * math.sin(2 * math.pi * 3 * f * t)
            + 0.12 * math.sin(2 * math.pi * 5 * f * t)
        ) / 1.87
        out.append(int(max(-32767, min(32767, v * env * amplitude * 32767))))
    return struct.pack(f"<{len(out)}h", *out)


class FakeCall(Call):
    """A call whose far end is a script and whose circuit is mu-law at 8 kHz."""

    def __init__(
        self,
        *,
        remote: str,
        direction: CallDirection,
        script: list[Utterance],
        sample_rate: int = PHONE_RATE,
        answer_after_ms: int = 400,
        speed: float = 1.0,
        clock: Any = None,
        sleep: Any = None,
    ) -> None:
        super().__init__(
            remote=remote, direction=direction, sample_rate=sample_rate, clock=clock
        )
        self._script = list(script)
        self._answer_after_ms = answer_after_ms
        self._speed = max(0.001, float(speed))
        self._sleep = sleep or asyncio.sleep
        self._driver: asyncio.Task | None = None
        self._carrier_task: asyncio.Task | None = None
        #: Set once she has been audible; cleared when the far end takes a turn.
        self._she_speaks = asyncio.Event()
        self._her_last_loud_at: float | None = None
        self._her_speech_started: float | None = None
        self._her_voiced_ms: float = 0.0
        self.spans: list[SpokenSpan] = []
        self._out: deque[bytes] = deque()
        self.dtmf_sent: list[str] = []

    # -- her audio arriving --------------------------------------------------

    async def send_audio(self, frame: AudioFrame) -> None:
        """Her synthesized voice, degraded to what the far end really hears."""
        await super().send_audio(frame)
        wire = to_phone(frame.pcm, frame.sample_rate)
        now = self._clock()
        if _is_voiced(wire):
            gap_ms = (
                (now - self._her_last_loud_at) * 1000.0
                if self._her_last_loud_at is not None
                else None
            )
            starting_a_run = gap_ms is None or gap_ms > SPEECH_RUN_GAP_MS
            if starting_a_run:
                # New run: either her first sound, or the first sound after a
                # gap long enough that the previous run was a separate thing.
                self._her_speech_started = now
                self._her_voiced_ms = 0.0
            self._she_speaks.set()
            self._her_last_loud_at = now
            self._her_voiced_ms += frame.duration_ms

    def her_silence_ms(self) -> float:
        """How long she has been quiet — the counterpart's turn-taking signal.

        Measured from her last *audible* frame, not from a quiet one: she stops
        transmitting between phrases, so absence of sound has to count as
        silence or the far end waits forever for a frame that never comes.
        """
        if not self._she_speaks.is_set() or self._her_last_loud_at is None:
            return 0.0
        return (self._clock() - self._her_last_loud_at) * 1000.0

    def her_speech_ms(self) -> float:
        """How long the *current* run of her speech has been going.

        Deliberately not "time since she first made any sound": a listener
        interrupts because of what you are saying now, and a backchannel is not
        a turn. Measuring from the "mm-hm" made the scripted interruption land
        on the same millisecond as the answer it was meant to cut into.
        """
        if not self._she_speaks.is_set() or self._her_speech_started is None:
            return 0.0
        return (self._clock() - self._her_speech_started) * 1000.0

    def _reset_her_turn(self) -> None:
        self._she_speaks.clear()
        self._her_last_loud_at = None
        self._her_speech_started = None
        self._her_voiced_ms = 0.0

    @property
    def far_end_speaking(self) -> bool:
        return bool(self._out)

    # -- verbs ---------------------------------------------------------------

    async def answer(self) -> None:
        if self.state is not CallState.RINGING:
            raise RuntimeError(f"cannot answer from {self.state}")
        self._set_state(CallState.ACTIVE)

    async def hangup(self, reason: EndReason = EndReason.LOCAL_HANGUP) -> None:
        if not self.live:
            return
        self._set_state(CallState.ENDED, reason)
        await self._shutdown()

    async def send_dtmf(self, digits: str) -> None:
        self.dtmf_sent.append(digits)

    async def _shutdown(self) -> None:
        """Stop the carrier and the script. Safe to call from either of them."""
        current = asyncio.current_task()
        for task in (self._carrier_task, self._driver):
            if task is None or task.done() or task is current:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- the far end ---------------------------------------------------------

    async def _nap(self, ms: float) -> None:
        await self._sleep(max(0.0, ms) / 1000.0 * self._speed)

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._driver = loop.create_task(self._run())
        self._carrier_task = loop.create_task(self._carrier())

    async def _carrier(self) -> None:
        """The circuit, always on.

        A phone line does not go away between words — it carries comfort noise.
        Endpointing depends on that: a turn ends because quiet frames keep
        arriving, not because frames stop. Emitting silence here is what makes
        the bench a circuit rather than a message queue.
        """
        step = frame_samples(self.sample_rate) * 2
        pacer = FramePacer(FRAME_MS, clock=self._clock)
        n = 0
        try:
            while self.live or self.state is CallState.IDLE:
                chunk = self._out.popleft() if self._out else comfort_noise(step, n)
                n += 1
                self._deliver(
                    AudioFrame(pcm=chunk, sample_rate=self.sample_rate, at=self._clock())
                )
                await pacer.wait()
        except asyncio.CancelledError:
            raise

    async def _run(self) -> None:
        try:
            self._set_state(CallState.DIALING)
            await self._nap(self._answer_after_ms * 0.4)
            self._set_state(CallState.RINGING)
            await self._nap(self._answer_after_ms * 0.6)
            if self.direction is CallDirection.OUTBOUND:
                # The far end picks up; inbound waits for her to answer.
                self._set_state(CallState.ACTIVE)
            else:
                while self.state is CallState.RINGING:
                    await self._nap(20)

            for utt in self._script:
                if not self.live:
                    return
                await self._await_cue(utt)
                if not self.live:
                    return
                await self._say(utt)
                if utt.tail_ms:
                    await self._nap(utt.tail_ms)
            # Script exhausted: the far end waits a beat, then rings off.
            await self._nap(1200)
            if self.live:
                self._set_state(CallState.ENDED, EndReason.REMOTE_HANGUP)
            await self._shutdown()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("fake call %s driver failed", self.id)
            self._set_state(CallState.FAILED, EndReason.ERROR)

    async def _await_cue(self, utt: Utterance) -> None:
        if utt.cue is Cue.AT_TIME:
            elapsed = (self._clock() - self.stats.started_at) * 1000.0
            await self._nap(max(0.0, utt.offset_ms - elapsed))
            return
        if utt.cue is Cue.INTERRUPT:
            # Wait until she has been speaking for offset_ms, then cut in.
            while self.live and self.her_speech_ms() < utt.offset_ms:
                await self._nap(FRAME_MS)
            return
        # AFTER_HER_TURN: she must actually say something, stop, and stay
        # stopped for offset_ms. The length test matters — a receptionist does
        # not start talking because you said "mm-hm", and without it the far end
        # treats every backchannel as a completed turn and talks over the answer.
        while self.live:
            if (
                self._she_speaks.is_set()
                and self._her_voiced_ms >= MIN_TURN_MS
                and self.her_silence_ms() >= utt.offset_ms
            ):
                return
            await self._nap(FRAME_MS)

    def _utterance_pcm(self, utt: Utterance) -> bytes:
        """Voiced audio, with a hesitation gap spliced in if the script asks."""
        seed = len(self.spans)
        if utt.hesitate_ms <= 0 or not (0 < utt.hesitate_at_ms < utt.duration_ms):
            return voiced_pcm(utt.duration_ms, self.sample_rate, seed=seed)
        head = voiced_pcm(utt.hesitate_at_ms, self.sample_rate, seed=seed)
        gap = comfort_noise(
            frame_samples(self.sample_rate) * 2 * max(1, utt.hesitate_ms // FRAME_MS),
            seed=seed + 41,
        )
        tail = voiced_pcm(
            utt.duration_ms - utt.hesitate_at_ms, self.sample_rate, seed=seed + 7
        )
        return head + gap + tail

    async def _say(self, utt: Utterance) -> None:
        span = SpokenSpan(text=utt.text, start=self._clock())
        self.spans.append(span)
        self._reset_her_turn()
        pcm = self._utterance_pcm(utt)
        step = frame_samples(self.sample_rate) * 2
        for off in range(0, len(pcm), step):
            chunk = pcm[off : off + step]
            if len(chunk) < step:
                chunk = chunk + b"\x00" * (step - len(chunk))
            self._out.append(chunk)
        # The carrier drains the queue at wire speed; wait for it to finish.
        while self._out and self.live:
            await self._nap(FRAME_MS)
        span.end = self._clock()


@lru_cache(maxsize=512)
def comfort_noise(n_bytes: int, seed: int = 0, level: float = 0.0018) -> bytes:
    """Line hiss — well under any endpointer threshold, but never digital zero.

    Real circuits are never silent, and an endpointer tuned against perfect
    zeros is tuned against a line that does not exist. This also gives the
    adaptive noise floor something to track.
    """
    n = n_bytes // 2
    amp = int(level * 32767)
    state = (seed * 1103515245 + 12345) & 0x7FFFFFFF
    out: list[int] = []
    for _ in range(n):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(((state >> 16) % (2 * amp + 1)) - amp)
    return struct.pack(f"<{len(out)}h", *out)


def _is_voiced(pcm: bytes, threshold: float = 0.02) -> bool:
    from remedy.telephony.narrowband import rms

    return rms(pcm) >= threshold


@dataclass
class FakeBackend:
    """Bench transport. Deterministic, offline, no hardware, no minutes spent."""

    script: list[Utterance] = field(default_factory=list)
    name: str = "fake"
    sample_rate: int = PHONE_RATE
    answer_after_ms: int = 400
    #: <1.0 runs the script faster than real time (tests); 1.0 is wall clock.
    speed: float = 1.0
    clock: Any = None
    sleep: Any = None
    _incoming: asyncio.Queue = field(default_factory=asyncio.Queue, init=False)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            outbound=True,
            inbound=True,
            dtmf_send=True,
            dtmf_receive=True,
            sms=False,
            full_duplex=True,
            simulated=True,
            sample_rate=self.sample_rate,
        )

    def _make(self, remote: str, direction: CallDirection) -> FakeCall:
        call = FakeCall(
            remote=remote,
            direction=direction,
            script=self.script,
            sample_rate=self.sample_rate,
            answer_after_ms=self.answer_after_ms,
            speed=self.speed,
            clock=self.clock,
            sleep=self.sleep,
        )
        call.start()
        return call

    async def place(self, number: str) -> FakeCall:
        return self._make(number, CallDirection.OUTBOUND)

    async def ring(self, number: str = "+15550100") -> FakeCall:
        """Simulate an inbound call for Phase 2 work."""
        call = self._make(number, CallDirection.INBOUND)
        await self._incoming.put(call)
        return call

    async def incoming(self) -> AsyncIterator[FakeCall]:
        while True:
            yield await self._incoming.get()
