"""The transport abstraction every backend has to satisfy."""

from __future__ import annotations

import asyncio

import pytest

from remedy.telephony.line import (
    AudioFrame,
    Call,
    CallDirection,
    CallState,
    Capabilities,
    EndReason,
    Line,
    silence,
)
from remedy.telephony.narrowband import frame_bytes


def _call(**kw) -> Call:
    return Call(remote="+15550100", direction=CallDirection.OUTBOUND, **kw)


def test_silence_frame_is_one_frame_long():
    frame = silence(8000)
    assert len(frame.pcm) == frame_bytes(8000)
    assert frame.duration_ms == 20.0


def test_live_covers_only_in_flight_states():
    call = _call()
    assert not call.live
    call._set_state(CallState.RINGING)
    assert call.live
    call._set_state(CallState.ACTIVE)
    assert call.live
    call._set_state(CallState.ENDED)
    assert not call.live


def test_end_reason_recorded_once_and_duration_stops():
    call = _call()
    call._set_state(CallState.ACTIVE)
    call._set_state(CallState.ENDED, EndReason.REMOTE_HANGUP)
    assert call.end_reason is EndReason.REMOTE_HANGUP
    assert call.stats.ended_at > 0
    settled = call.stats.duration_s
    assert call.stats.duration_s == settled


@pytest.mark.asyncio
async def test_audio_in_stops_when_the_call_ends():
    call = _call()
    call._set_state(CallState.ACTIVE)
    call._deliver(AudioFrame(pcm=b"\x00" * 320))
    call._set_state(CallState.ENDED)

    got = [f async for f in call.audio_in()]
    assert len(got) == 1


@pytest.mark.asyncio
async def test_full_inbound_queue_drops_oldest_rather_than_blocking():
    """A stalled consumer must never back-pressure a live call: the transport
    keeps running and we lose the oldest frame, not the newest."""
    call = _call()
    call._set_state(CallState.ACTIVE)
    for i in range(300):
        call._deliver(AudioFrame(pcm=bytes([i % 256]) * 320))
    assert call._inbound.qsize() <= 256
    first = await call._inbound.get()
    assert first.pcm[0] != 0  # the earliest frames were discarded


@pytest.mark.asyncio
async def test_wait_state_returns_current_state_on_timeout():
    call = _call()
    call._set_state(CallState.RINGING)
    assert await call.wait_state(timeout=0.01) is CallState.RINGING


@pytest.mark.asyncio
async def test_wait_state_wakes_on_transition():
    call = _call()
    waiter = asyncio.create_task(call.wait_state(timeout=1.0))
    await asyncio.sleep(0)
    call._set_state(CallState.ACTIVE)
    assert await waiter is CallState.ACTIVE


def test_capabilities_not_ready_when_something_is_missing():
    assert not Capabilities(outbound=True, missing=("no bluetooth radio",)).ready
    assert Capabilities(outbound=True).ready
    assert not Capabilities().ready


@pytest.mark.asyncio
async def test_line_refuses_a_second_concurrent_call():
    """One call at a time is a product boundary (docs/TELEPHONY.md), so it is
    enforced here rather than trusted to callers."""

    class _Backend:
        name = "stub"

        def capabilities(self) -> Capabilities:
            return Capabilities(outbound=True, simulated=True)

        async def place(self, number: str) -> Call:
            call = _call()
            call._set_state(CallState.ACTIVE)
            return call

    line = Line(backend=_Backend())
    await line.place("+15550100")
    with pytest.raises(RuntimeError):
        await line.place("+15550101")


@pytest.mark.asyncio
async def test_line_places_again_after_the_first_call_ends():
    class _Backend:
        name = "stub"

        def capabilities(self) -> Capabilities:
            return Capabilities(outbound=True, simulated=True)

        async def place(self, number: str) -> Call:
            return _call()

    line = Line(backend=_Backend())
    first = await line.place("+15550100")
    first._set_state(CallState.ENDED)
    assert await line.place("+15550101") is not first


@pytest.mark.asyncio
async def test_a_backend_that_cannot_describe_itself_is_treated_as_real(tmp_path):
    """Fail closed. Being wrong this way costs a prompt; the other way it is an
    unagreed call to a stranger."""

    class _Mystery:
        name = "mystery"

        async def place(self, number: str) -> Call:
            raise AssertionError("dialled without knowing what this backend is")

    # Any un-agreed terms layer is enough to stop it; the point is that it stops.
    with pytest.raises(RuntimeError):
        await Line(backend=_Mystery(), home=tmp_path).place("+15550100")
