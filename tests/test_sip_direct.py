"""Loopback SIP-without-PSTN: packets stay on 127.0.0.1, terms do not apply."""

from __future__ import annotations

import asyncio

from remedy.telephony.backends.sip_direct import SipDirectBackend
from remedy.telephony.line import AudioFrame, CallState, EndReason, Line
from remedy.telephony.narrowband import PHONE_RATE, frame_bytes


def test_capabilities_are_simulated():
    b = SipDirectBackend()
    caps = b.capabilities()
    assert caps.simulated is True
    assert caps.outbound is True
    assert caps.ready is True


def test_place_does_not_need_terms():
    """Line.place would raise without terms on a real trunk; loopback is exempt."""
    b = SipDirectBackend()
    line = Line(backend=b)

    async def go():
        call = await line.place("loopback")
        assert call.state is CallState.ACTIVE
        await call.hangup(EndReason.LOCAL_HANGUP)
        assert call.state is CallState.ENDED

    asyncio.run(go())


def test_loopback_echoes_audio():
    b = SipDirectBackend()

    async def go():
        call = await b.place("echo")
        pcm = b"\x11\x00" * (frame_bytes(PHONE_RATE) // 2)
        await call.send_audio(AudioFrame(pcm=pcm, sample_rate=PHONE_RATE))

        async def first():
            async for frame in call.audio_in():
                return frame
            raise AssertionError("no audio")

        got = await asyncio.wait_for(first(), timeout=1.5)
        assert got.pcm == pcm
        await call.hangup()

    asyncio.run(go())
