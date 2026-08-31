"""Rendezvous (public-broker mobile data) tests.

Covers the minimal MQTT 3.1.1 codec, the QR ``rdv=`` value, and a full
roundtrip of Noise records through an in-process fake broker — proving the
PC/phone rendezvous bridge works without any real public broker.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import struct

import pytest

from remedy.connect.rdv import (
    PUBLIC_RDV_ENDPOINTS,
    MqttError,
    MqttMessage,
    MqttSession,
    RendezvousSession,
    build_connect,
    build_publish,
    build_puback,
    build_subscribe,
    decode_varint,
    encode_varint,
    parse_connack,
    parse_publish,
    parse_rdv_endpoints,
    parse_suback,
    rdv_qr_value,
    rdv_topic,
)


# ---------------------------------------------------------------------------
# Codec unit tests.
# ---------------------------------------------------------------------------


def test_varint_roundtrip() -> None:
    for n in (0, 1, 127, 128, 16383, 16384, 2097151, 268435455):
        enc = encode_varint(n)
        value, consumed = decode_varint(enc)
        assert value == n
        assert consumed == len(enc)


def test_varint_out_of_range() -> None:
    with pytest.raises(ValueError):
        encode_varint(-1)
    with pytest.raises(ValueError):
        encode_varint(268435456)


def test_connack_codes() -> None:
    assert parse_connack(b"\x20\x02\x00\x00") == 0
    assert parse_connack(b"\x20\x02\x00\x05") == 5
    with pytest.raises(ValueError):
        parse_connack(b"\x10\x00")


def test_subscribe_suback() -> None:
    pkt = build_subscribe(7, ["a/b", "c/d"])
    assert pkt[0] == 0x82
    pid, codes = parse_suback(b"\x90\x03\x00\x07\x01")
    assert pid == 7
    assert codes == [1]


def test_publish_parse_qos1() -> None:
    topic = "remedy/" + os.urandom(16).hex() + "/pc"
    pkt = build_publish(9, topic, b"xyz")
    msg = parse_publish(pkt)
    assert msg.topic == topic
    assert msg.payload == b"xyz"
    assert msg.qos == 1
    assert msg.packet_id == 9


def test_publish_parse_qos0() -> None:
    pkt = build_publish(0, "t", b"v", qos=0)
    msg = parse_publish(pkt)
    assert msg.qos == 0
    assert msg.payload == b"v"
    assert msg.packet_id == 0


def test_publish_parse_rejects_bad() -> None:
    with pytest.raises(ValueError):
        parse_publish(b"\x00\x00")
    with pytest.raises(ValueError):
        parse_publish(b"\x36\x02\x00\x01")  # QoS 3 flag


def test_puback_packet() -> None:
    assert build_puback(0x1234) == b"\x40\x02\x12\x34"


def test_rdv_qr_value_roundtrip() -> None:
    value = rdv_qr_value()
    assert ";" in value
    assert parse_rdv_endpoints(value) == list(PUBLIC_RDV_ENDPOINTS)
    with pytest.raises(ValueError):
        parse_rdv_endpoints("")
    with pytest.raises(ValueError):
        parse_rdv_endpoints(";;;")


def test_rdv_topic_shape() -> None:
    sid = bytes(range(16))
    assert rdv_topic(sid, "pc") == "remedy/" + sid.hex() + "/pc"
    assert rdv_topic(sid, "phone") == "remedy/" + sid.hex() + "/phone"
    with pytest.raises(ValueError):
        rdv_topic(sid, "bogus")
    with pytest.raises(ValueError):
        rdv_topic(b"short", "pc")


# ---------------------------------------------------------------------------
# In-process fake broker (minimal MQTT 3.1.1).
# ---------------------------------------------------------------------------


async def _read_packet(reader: asyncio.StreamReader) -> bytes | None:
    try:
        first = await reader.readexactly(1)
    except asyncio.IncompleteReadError:
        return None
    (b0,) = first
    multiplier = 1
    value = 0
    varint = b""
    while True:
        try:
            byte = await reader.readexactly(1)
        except asyncio.IncompleteReadError:
            return None
        varint += byte
        b = byte[0]
        value += (b & 0x7F) * multiplier
        if (b & 0x80) == 0:
            break
        multiplier *= 128
    body = b""
    if value:
        try:
            body = await reader.readexactly(value)
        except asyncio.IncompleteReadError:
            return None
    return bytes([b0]) + varint + body


def _sub_topics(pkt: bytes) -> list[str]:
    # SUBSCRIBE: fixed header + varint, then pktid(2), then topic entries.
    _, consumed = decode_varint(pkt, 1)
    body = pkt[1 + consumed :]
    offset = 2  # packet id
    topics: list[str] = []
    while offset + 3 <= len(body):
        tlen = struct.unpack("!H", body[offset : offset + 2])[0]
        offset += 2
        topics.append(body[offset : offset + tlen].decode("utf-8", "replace"))
        offset += tlen + 1  # qos byte
    return topics


class FakeBroker:
    """Tiny MQTT 3.1.1 broker: subscribe/publish QoS1 forwarding."""

    def __init__(self, connack_code: int = 0) -> None:
        self.connack_code = connack_code
        self.subs: dict[str, set[asyncio.StreamWriter]] = {}
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._on_conn, "127.0.0.1", 0)
        self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def stop(self) -> None:
        server = self.server
        self.server = None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()

    async def _on_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await _read_packet(reader)  # CONNECT
            writer.write(b"\x20\x02\x00" + bytes([self.connack_code]))
            await writer.drain()
            if self.connack_code != 0:
                writer.close()
                return
            while True:
                pkt = await _read_packet(reader)
                if pkt is None:
                    break
                kind = pkt[0] & 0xF0
                if kind == 0x80:  # SUBSCRIBE
                    topics = _sub_topics(pkt)
                    pid = struct.unpack("!H", pkt[2:4])[0]
                    for topic in topics:
                        self.subs.setdefault(topic, set()).add(writer)
                    body = struct.pack("!H", pid) + (b"\x01" * len(topics))
                    writer.write(bytes([0x90]) + bytes([len(body)]) + body)
                    await writer.drain()
                elif kind == 0x30:  # PUBLISH
                    msg = parse_publish(pkt)
                    if msg.qos > 0:
                        writer.write(build_puback(msg.packet_id))
                        await writer.drain()
                    for sub in list(self.subs.get(msg.topic, ())):
                        if sub is writer:
                            continue
                        with contextlib.suppress(Exception):
                            sub.write(pkt)
                            await sub.drain()
                elif kind == 0xC0:  # PINGREQ
                    writer.write(b"\xd0\x00")
                    await writer.drain()
                elif kind == 0xE0:  # DISCONNECT
                    break
        except Exception:
            pass
        finally:
            for writers in self.subs.values():
                writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()


# ---------------------------------------------------------------------------
# End-to-end rendezvous roundtrip.
# ---------------------------------------------------------------------------


async def _make_session(
    port: int, sid: bytes, role: str
) -> tuple[RendezvousSession, asyncio.StreamReader, asyncio.StreamWriter]:
    mqtt = MqttSession("127.0.0.1", port, timeout=5.0)
    await mqtt.connect()
    session = RendezvousSession(mqtt, sid, role=role)
    reader, writer = await session.open()
    return session, reader, writer


@pytest.mark.asyncio
async def test_rdv_roundtrip_pc_phone() -> None:
    broker = FakeBroker()
    port = await broker.start()
    pc: RendezvousSession | None = None
    phone: RendezvousSession | None = None
    try:
        sid = os.urandom(16)
        pc, pc_reader, pc_writer = await _make_session(port, sid, "pc")
        phone, ph_reader, ph_writer = await _make_session(port, sid, "phone")

        # PC → phone (u32be-framed Noise record).
        payload = b"hello-from-pc-" + os.urandom(32)
        pc_writer.write(struct.pack("!I", len(payload)) + payload)
        await pc_writer.drain()
        got = await asyncio.wait_for(ph_reader.readexactly(4 + len(payload)), timeout=5.0)
        assert got[4:] == payload

        # Phone → PC.
        reply = b"reply-from-phone-" + os.urandom(48)
        ph_writer.write(struct.pack("!I", len(reply)) + reply)
        await ph_writer.drain()
        got2 = await asyncio.wait_for(pc_reader.readexactly(4 + len(reply)), timeout=5.0)
        assert got2[4:] == reply

        # Large record (>127 bytes) exercises the multi-byte MQTT varint.
        big = os.urandom(300)
        pc_writer.write(struct.pack("!I", len(big)) + big)
        await pc_writer.drain()
        got3 = await asyncio.wait_for(ph_reader.readexactly(4 + len(big)), timeout=5.0)
        assert got3[4:] == big
    finally:
        if pc is not None:
            await pc.aclose()
        if phone is not None:
            await phone.aclose()
        await broker.stop()


@pytest.mark.asyncio
async def test_rdv_wrong_sid_does_not_meet() -> None:
    """Different session ids never rendezvous on the same broker."""
    broker = FakeBroker()
    port = await broker.start()
    a: RendezvousSession | None = None
    b: RendezvousSession | None = None
    try:
        a, a_reader, a_writer = await _make_session(port, os.urandom(16), "pc")
        b, b_reader, b_writer = await _make_session(port, os.urandom(16), "phone")

        a_writer.write(struct.pack("!I", 5) + b"aaaaa")
        await a_writer.drain()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(b_reader.readexactly(9), timeout=0.6)
    finally:
        if a is not None:
            await a.aclose()
        if b is not None:
            await b.aclose()
        await broker.stop()


@pytest.mark.asyncio
async def test_mqtt_connect_refused() -> None:
    broker = FakeBroker(connack_code=5)
    port = await broker.start()
    try:
        mqtt = MqttSession("127.0.0.1", port, timeout=3.0)
        with pytest.raises(MqttError):
            await mqtt.connect()
    finally:
        await broker.stop()


@pytest.mark.asyncio
async def test_rdv_broker_down_is_graceful() -> None:
    """A dead broker raises, never hangs: supervisor backoff covers the rest."""
    mqtt = MqttSession("127.0.0.1", 1, timeout=1.0)  # port 1: nothing listens
    with pytest.raises(Exception):
        await asyncio.wait_for(mqtt.connect(), timeout=3.0)
