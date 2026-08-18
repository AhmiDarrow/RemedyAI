"""The line — one abstraction every transport implements.

Nothing above this file knows whether audio is riding a Bluetooth hands-free
link to the owner's phone, a SIP trunk, or a bench fixture. That is the whole
point: Phase 1 ships the phone bridge, Phase 3 adds her own number, and the
pipeline that speaks does not change a line of code between them.

Frames are 20 ms of 16-bit little-endian mono PCM, the RTP convention. At the
8 kHz a phone line actually carries that is 160 samples / 320 bytes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from remedy.telephony.narrowband import PHONE_RATE, frame_bytes

logger = logging.getLogger(__name__)


class CallState(StrEnum):
    IDLE = "idle"
    DIALING = "dialing"
    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class CallDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class EndReason(StrEnum):
    LOCAL_HANGUP = "local_hangup"
    REMOTE_HANGUP = "remote_hangup"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    ERROR = "error"
    #: The owner picked up the handset — she steps out, she does not fight for it.
    OWNER_TOOK_OVER = "owner_took_over"


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One 20 ms slice of the line."""

    pcm: bytes
    sample_rate: int = PHONE_RATE
    at: float = 0.0

    @property
    def duration_ms(self) -> float:
        return (len(self.pcm) / 2) / self.sample_rate * 1000.0


@dataclass(slots=True)
class CallStats:
    frames_in: int = 0
    frames_out: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0

    @property
    def duration_s(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.ended_at or time.monotonic()
        return max(0.0, end - self.started_at)


class Call:
    """A live call. Backends subclass and fill in the transport verbs.

    The base class owns everything transport-independent: state transitions,
    the inbound frame queue, DTMF collection, and the end reason. A backend
    that only knows how to move bytes gets the rest for free.
    """

    def __init__(
        self,
        *,
        remote: str,
        direction: CallDirection,
        sample_rate: int = PHONE_RATE,
        call_id: str = "",
        clock: Any = None,
    ) -> None:
        self.id = call_id or uuid4().hex[:12]
        self.remote = remote
        self.direction = direction
        self.sample_rate = sample_rate
        self.state = CallState.IDLE
        self.end_reason: EndReason | None = None
        self.stats = CallStats()
        self._clock = clock or time.monotonic
        self._inbound: asyncio.Queue[AudioFrame | None] = asyncio.Queue(maxsize=256)
        self._state_waiters: list[asyncio.Future[CallState]] = []
        self._dtmf_in: list[str] = []

    # -- state ---------------------------------------------------------------

    def _set_state(self, state: CallState, reason: EndReason | None = None) -> None:
        if self.state == state:
            return
        prev, self.state = self.state, state
        if reason is not None:
            self.end_reason = reason
        if state is CallState.ACTIVE and not self.stats.started_at:
            self.stats.started_at = self._clock()
        if state in (CallState.ENDED, CallState.FAILED):
            self.stats.ended_at = self._clock()
            with contextlib.suppress(asyncio.QueueFull):
                self._inbound.put_nowait(None)
        logger.debug("call %s: %s -> %s", self.id, prev, state)
        for fut in self._state_waiters:
            if not fut.done():
                fut.set_result(state)
        self._state_waiters.clear()

    @property
    def live(self) -> bool:
        return self.state in (CallState.DIALING, CallState.RINGING, CallState.ACTIVE)

    async def wait_state(self, timeout: float | None = None) -> CallState:
        """Block until the next state change (or timeout, returning current)."""
        fut: asyncio.Future[CallState] = asyncio.get_running_loop().create_future()
        self._state_waiters.append(fut)
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            return self.state

    # -- audio ---------------------------------------------------------------

    def _deliver(self, frame: AudioFrame) -> None:
        """Backend -> pipeline. Drops the oldest frame rather than blocking the
        transport: a stalled consumer must never back-pressure a live call."""
        self.stats.frames_in += 1
        try:
            self._inbound.put_nowait(frame)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._inbound.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._inbound.put_nowait(frame)
            logger.warning("call %s: inbound queue full, dropped a frame", self.id)

    async def audio_in(self) -> AsyncIterator[AudioFrame]:
        """Frames from the far end, until the call ends."""
        while True:
            frame = await self._inbound.get()
            if frame is None:
                return
            yield frame

    async def send_audio(self, frame: AudioFrame) -> None:
        """Pipeline -> backend. Override in the backend."""
        self.stats.frames_out += 1

    # -- verbs ---------------------------------------------------------------

    async def answer(self) -> None:
        raise NotImplementedError

    async def hangup(self, reason: EndReason = EndReason.LOCAL_HANGUP) -> None:
        raise NotImplementedError

    async def send_dtmf(self, digits: str) -> None:
        raise NotImplementedError

    def dtmf_received(self) -> str:
        """Digits the far end pressed (IVR confirmations, callbacks)."""
        return "".join(self._dtmf_in)

    def __repr__(self) -> str:
        return f"<Call {self.id} {self.direction} {self.remote} {self.state}>"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a backend can actually do, so callers ask instead of assume."""

    outbound: bool = False
    inbound: bool = False
    dtmf_send: bool = False
    dtmf_receive: bool = False
    sms: bool = False
    #: Audio the far end hears is full-duplex (not speakerphone loopback).
    full_duplex: bool = True
    #: A bench fixture rather than a real line. Simulated calls reach nobody, so
    #: they are exempt from the terms gate — which is precisely why the flag
    #: must be set by the backend and never inferred.
    simulated: bool = False
    sample_rate: int = PHONE_RATE
    #: Human-readable gaps, surfaced verbatim in conversation during setup.
    missing: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.missing and (self.outbound or self.inbound)


class LineBackend(Protocol):
    """A transport. Implemented by fake, bluetooth_hfp, sip_baresip, sip_direct."""

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def capabilities(self) -> Capabilities: ...

    async def place(self, number: str) -> Call: ...

    def incoming(self) -> AsyncIterator[Call]: ...


@dataclass(slots=True)
class Line:
    """A backend plus the one call it is allowed to have.

    One call at a time is a product boundary, not a technical limit
    (``docs/TELEPHONY.md`` — no dialing lists, no campaigns).
    """

    backend: Any
    current: Call | None = field(default=None)

    #: Where consent lives. None uses REMEDY_HOME / ~/.remedy.
    home: Any = None

    async def place(self, number: str) -> Call:
        if self.current is not None and self.current.live:
            raise RuntimeError("a call is already in progress")
        self._require_terms()
        call = await self.backend.place(number)
        self.current = call
        return call

    def _require_terms(self) -> None:
        """No real call before the owner has agreed to the phone terms.

        Enforced at the choke point rather than in each backend, so a new
        transport cannot forget. Bench fixtures declare themselves simulated and
        are exempt — otherwise nobody could test anything without agreeing to
        terms about calls they are not making.
        """
        from remedy.telephony import consent

        # Fail closed: a backend that cannot tell us what it is gets treated as
        # a real line. The cost of being wrong in that direction is a prompt;
        # the other way it is an unagreed call to a stranger.
        try:
            simulated = bool(self.backend.capabilities().simulated)
        except AttributeError:
            simulated = False
        if simulated:
            return
        consent.require(self.home)

    async def hangup(self, reason: EndReason = EndReason.LOCAL_HANGUP) -> None:
        if self.current is not None and self.current.live:
            await self.current.hangup(reason)

    def capabilities(self) -> Capabilities:
        return self.backend.capabilities()


def silence(sample_rate: int = PHONE_RATE) -> AudioFrame:
    """One frame of nothing — playout keeps a steady cadence between phrases."""
    return AudioFrame(pcm=b"\x00" * frame_bytes(sample_rate), sample_rate=sample_rate)
