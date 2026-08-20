"""SIP-without-PSTN loopback — Phase 0 leftover, no trunk, nobody is called.

Two local UDP ports exchange 20 ms PCM frames the way RTP would. The far
end of a ``loopback`` / ``echo`` place() is this process, so HQ voice and
the duplex pipeline can be exercised on a real datagram path without
minutes, hardware, or a SIP account.

``capabilities.simulated`` is True: the terms gate does not apply, because
the packets never leave 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from remedy.telephony.line import (
    AudioFrame,
    Call,
    CallDirection,
    CallState,
    Capabilities,
    EndReason,
)
from remedy.telephony.narrowband import PHONE_RATE

logger = logging.getLogger(__name__)


class _EchoProto(asyncio.DatagramProtocol):
    def __init__(self, call: SipDirectCall) -> None:
        self.call = call

    def datagram_received(self, data: bytes, _addr: tuple[str, int]) -> None:
        if not self.call.live or not data:
            return
        self.call._deliver(
            AudioFrame(pcm=data, sample_rate=self.call.sample_rate, at=self.call._clock())
        )


class SipDirectCall(Call):
    def __init__(
        self,
        *,
        remote: str,
        direction: CallDirection,
        sample_rate: int = PHONE_RATE,
        clock: Any = None,
        peer: tuple[str, int] | None = None,
    ) -> None:
        super().__init__(
            remote=remote,
            direction=direction,
            sample_rate=sample_rate,
            clock=clock,
        )
        self._peer = peer
        self._local: tuple[str, int] | None = None
        self._transport: asyncio.DatagramTransport | None = None

    async def bind(self, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        loop = asyncio.get_running_loop()
        transport, _proto = await loop.create_datagram_endpoint(
            lambda: _EchoProto(self),
            local_addr=(host, port),
        )
        self._transport = transport
        sockname = transport.get_extra_info("sockname")
        self._local = (str(sockname[0]), int(sockname[1]))
        return self._local

    def set_peer(self, peer: tuple[str, int]) -> None:
        self._peer = peer

    async def answer(self) -> None:
        if self.state is CallState.RINGING:
            self._set_state(CallState.ACTIVE)

    async def hangup(self, reason: EndReason = EndReason.LOCAL_HANGUP) -> None:
        if not self.live and self.state is not CallState.RINGING:
            return
        self._set_state(CallState.ENDED, reason)
        await self._close()

    async def send_dtmf(self, digits: str) -> None:
        self._dtmf_in.extend(list(digits or ""))

    async def send_audio(self, frame: AudioFrame) -> None:
        await super().send_audio(frame)
        if not frame.pcm or not self.live:
            return
        # Same-process loopback: do not depend on OS UDP-to-self.
        if self._peer is None or self._peer == self._local:
            self._deliver(
                AudioFrame(
                    pcm=frame.pcm,
                    sample_rate=frame.sample_rate,
                    at=self._clock(),
                )
            )
            return
        if self._transport is None:
            return
        try:
            self._transport.sendto(frame.pcm, self._peer)
        except OSError as exc:
            logger.debug("sip_direct send failed: %s", exc)

    async def _close(self) -> None:
        t = self._transport
        self._transport = None
        if t is not None:
            t.close()


class SipDirectBackend:
    """Local UDP loopback. ``place('loopback')`` talks to itself."""

    name = "sip_direct"

    def __init__(self, *, sample_rate: int = PHONE_RATE) -> None:
        self.sample_rate = sample_rate
        self._incoming: asyncio.Queue[SipDirectCall] = asyncio.Queue()

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
            full_duplex=True,
            simulated=True,
            sample_rate=self.sample_rate,
        )

    async def place(self, number: str) -> SipDirectCall:
        call = SipDirectCall(
            remote=number or "loopback",
            direction=CallDirection.OUTBOUND,
            sample_rate=self.sample_rate,
        )
        addr = await call.bind()
        # Echo: packets we send come straight back as the far end.
        call.set_peer(addr)
        call._set_state(CallState.DIALING)
        call._set_state(CallState.RINGING)
        call._set_state(CallState.ACTIVE)
        return call

    async def incoming(self) -> AsyncIterator[SipDirectCall]:
        while True:
            yield await self._incoming.get()
