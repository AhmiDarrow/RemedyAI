"""Zero-setup public rendezvous for Grove Connect (mobile data).

The owner-relay design needs a public host the user runs. That is the
"advanced install process" we are removing: both the PC and the phone already
dial *out*, so they can instead meet on a **public MQTT broker** — no account,
no binary, no VPS.

The broker is a blind byte-splice rendezvous:

* topic name  = ``remedy/<16-byte session id hex>/<side>`` (unguessable, no PII)
* payload     = one Noise record (ciphertext only)
* trust model = identical to the owner relay: the broker sees only a random
  session id and encrypted frames; it can never read chats.

Only MQTT 3.1.1 packets we need are implemented (CONNECT/CONACK, SUBSCRIBE/
SUBACK, PUBLISH QoS1/PUBACK, PINGREQ/PINGRESP, DISCONNECT). No third-party
dependency, no downloaded binaries.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Ordered public broker list. The PC holds a rendezvous on EVERY broker; the
# phone tries them in this order and meets the PC on the first both can reach.
PUBLIC_RDV_ENDPOINTS: tuple[tuple[str, int], ...] = (
    ("broker.emqx.io", 1883),
    ("broker.hivemq.com", 1883),
    ("test.mosquitto.org", 1883),
)

KEEPALIVE_S = 30
PING_EVERY_S = 12.0
MAX_RECORD = 65536  # matches relay.MAX_PAYLOAD
CLIENT_ID_LEN = 16

# MQTT packet types (fixed-header high nibble).
_MQTT_CONNECT = 0x10
_MQTT_CONNACK = 0x20
_MQTT_PUBLISH = 0x30
_MQTT_PUBACK = 0x40
_MQTT_SUBSCRIBE = 0x80
_MQTT_SUBACK = 0x90
_MQTT_PINGREQ = 0xC0
_MQTT_PINGRESP = 0xD0
_MQTT_DISCONNECT = 0xE0


# --------------------------------------------------------------------------
# Packet codec (pure functions — unit-tested without a broker).
# --------------------------------------------------------------------------


def encode_varint(value: int) -> bytes:
    """MQTT remaining-length varint (1..4 bytes)."""
    if value < 0 or value > 268435455:
        raise ValueError(f"varint out of range: {value}")
    out = bytearray()
    while True:
        byte = value % 128
        value //= 128
        if value > 0:
            byte |= 0x80
        out.append(byte)
        if value == 0:
            return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Return ``(value, bytes_consumed)`` from ``data[offset:]``."""
    value = 0
    multiplier = 1
    i = offset
    while True:
        if i >= len(data):
            raise ValueError("truncated varint")
        byte = data[i]
        value += (byte & 0x7F) * multiplier
        if (byte & 0x80) == 0:
            return value, i - offset + 1
        if multiplier > 128 * 128 * 128:
            raise ValueError("varint too long")
        multiplier *= 128
        i += 1


def _utf8_field(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 65535:
        raise ValueError("MQTT string too long")
    return struct.pack("!H", len(raw)) + raw


def build_connect(client_id: str, keepalive: int = KEEPALIVE_S) -> bytes:
    body = (
        b"\x00\x04MQTT"  # protocol name
        b"\x04"  # level 3.1.1
        b"\x02"  # flags: clean session
        + struct.pack("!H", int(keepalive))
        + _utf8_field(client_id)
    )
    return bytes([_MQTT_CONNECT]) + encode_varint(len(body)) + body


def parse_connack(data: bytes) -> int:
    """Return MQTT return code (0 = accepted). Raises ValueError otherwise."""
    if len(data) < 4 or data[0] != _MQTT_CONNACK:
        raise ValueError("not a CONNACK")
    remaining, consumed = decode_varint(data, 1)
    if remaining < 2 or 1 + consumed + 2 > len(data):
        raise ValueError("truncated CONNACK")
    _flags = data[1 + consumed]
    code = data[2 + consumed]
    return code


def build_subscribe(packet_id: int, topics: list[str], qos: int = 1) -> bytes:
    body = struct.pack("!H", packet_id & 0xFFFF)
    for topic in topics:
        body += _utf8_field(topic) + bytes([qos & 0x03])
    return bytes([_MQTT_SUBSCRIBE | 0x02]) + encode_varint(len(body)) + body


def parse_suback(data: bytes) -> tuple[int, list[int]]:
    if len(data) < 5 or data[0] != _MQTT_SUBACK:
        raise ValueError("not a SUBACK")
    remaining, consumed = decode_varint(data, 1)
    body = data[1 + consumed :]
    if len(body) < 3 or len(body) != remaining:
        raise ValueError("truncated SUBACK")
    packet_id = struct.unpack("!H", body[:2])[0]
    codes = list(body[2:])
    return packet_id, codes


def build_publish(packet_id: int, topic: str, payload: bytes, qos: int = 1) -> bytes:
    if len(payload) > MAX_RECORD:
        raise ValueError("publish payload too large")
    body = _utf8_field(topic)
    if qos > 0:
        body += struct.pack("!H", packet_id & 0xFFFF)
    body += payload
    flags = 0x02 if qos == 1 else 0x00
    return bytes([_MQTT_PUBLISH | flags]) + encode_varint(len(body)) + body


@dataclass
class MqttMessage:
    topic: str
    payload: bytes
    qos: int = 0
    packet_id: int = 0


def parse_publish(data: bytes) -> MqttMessage:
    if len(data) < 2 or (data[0] & 0xF0) != _MQTT_PUBLISH:
        raise ValueError("not a PUBLISH")
    remaining, consumed = decode_varint(data, 1)
    body = data[1 + consumed :]
    if len(body) != remaining:
        raise ValueError("truncated PUBLISH")
    qos = (data[0] >> 1) & 0x03
    if qos == 3:
        raise ValueError("invalid PUBLISH QoS")
    tlen = struct.unpack("!H", body[:2])[0]
    if 2 + tlen > len(body):
        raise ValueError("truncated PUBLISH topic")
    topic = body[2 : 2 + tlen].decode("utf-8", "replace")
    offset = 2 + tlen
    packet_id = 0
    if qos > 0:
        if offset + 2 > len(body):
            raise ValueError("truncated PUBLISH id")
        packet_id = struct.unpack("!H", body[offset : offset + 2])[0]
        offset += 2
    return MqttMessage(topic=topic, payload=body[offset:], qos=qos, packet_id=packet_id)


def build_puback(packet_id: int) -> bytes:
    return bytes([_MQTT_PUBACK]) + b"\x02" + struct.pack("!H", packet_id & 0xFFFF)


_PINGREQ = bytes([_MQTT_PINGREQ, 0x00])
_PINGRESP = bytes([_MQTT_PINGRESP, 0x00])
_DISCONNECT = bytes([_MQTT_DISCONNECT, 0x00])


# --------------------------------------------------------------------------
# Asyncio MQTT session.
# --------------------------------------------------------------------------


class MqttError(Exception):
    pass


class MqttSession:
    """Minimal MQTT 3.1.1 client over asyncio streams. QoS1 only."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        client_id: str | None = None,
        keepalive: int = KEEPALIVE_S,
        timeout: float = 8.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.client_id = client_id or os.urandom(CLIENT_ID_LEN).hex()
        self.keepalive = keepalive
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pkt_id = 1
        self._pub_wait: asyncio.Future[None] | None = None
        self._closed = False

    async def connect(self) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        self._reader = reader
        self._writer = writer
        writer.write(build_connect(self.client_id, self.keepalive))
        await writer.drain()
        resp = await asyncio.wait_for(reader.readexactly(4), timeout=self.timeout)
        code = parse_connack(resp)
        if code != 0:
            self.close()
            raise MqttError(f"MQTT broker refused ({code})")

    async def subscribe(self, topics: list[str], qos: int = 1) -> None:
        writer = self._require_writer()
        packet_id = self._next_id()
        writer.write(build_subscribe(packet_id, topics, qos))
        await writer.drain()
        # SUBACK arrives on the same stream — read until we see it.
        while True:
            msg = await self._read_packet()
            if msg is None:
                raise MqttError("MQTT stream closed during SUBSCRIBE")
            if msg[0] == _MQTT_SUBACK:
                _, codes = parse_suback(msg)
                if any(code == 0x80 for code in codes):
                    raise MqttError("MQTT broker refused subscription")
                return
            if msg[0] == _MQTT_PUBLISH:
                await self._handle_inbound_publish(msg)

    async def publish(self, topic: str, payload: bytes, qos: int = 1) -> None:
        writer = self._require_writer()
        packet_id = self._next_id() if qos > 0 else 0
        waiter: asyncio.Future[None] | None = None
        if qos > 0:
            waiter = asyncio.get_running_loop().create_future()
            self._pub_wait = waiter
        writer.write(build_publish(packet_id, topic, payload, qos))
        await writer.drain()
        if waiter is not None:
            await asyncio.wait_for(waiter, timeout=20.0)

    async def _reader_loop(self) -> None:
        """Consume inbound packets (PUBLISH → callback, PUBACK → release)."""
        reader = self._reader
        if reader is None:
            return
        try:
            while not self._closed:
                msg = await self._read_packet()
                if msg is None:
                    break
                if msg[0] == _MQTT_PUBACK:
                    waiter = self._pub_wait
                    self._pub_wait = None
                    if waiter is not None and not waiter.done():
                        waiter.set_result(None)
                elif (msg[0] & 0xF0) == _MQTT_PUBLISH:
                    await self._handle_inbound_publish(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closed:
                logger.debug("rdv MQTT reader ended", exc_info=True)

    async def _handle_inbound_publish(self, msg: bytes) -> None:
        parsed = parse_publish(msg)
        if parsed.qos > 0:
            writer = self._require_writer()
            writer.write(build_puback(parsed.packet_id))
            await writer.drain()
        handler = self.on_message
        if handler is not None and parsed.topic:
            try:
                await handler(parsed.topic, parsed.payload)
            except Exception:
                logger.debug("rdv inbound handler failed", exc_info=True)

    async def _read_packet(self) -> bytes | None:
        reader = self._reader
        if reader is None:
            return None
        first = await reader.readexactly(1)
        if not first:
            return None
        (type_flags,) = first
        varint = b""
        multiplier = 1
        value = 0
        while True:
            byte = await reader.readexactly(1)
            if not byte:
                return None
            varint += byte
            b0 = byte[0]
            value += (b0 & 0x7F) * multiplier
            if (b0 & 0x80) == 0:
                break
            if multiplier > 128 * 128 * 128:
                raise MqttError("MQTT varint too long")
            multiplier *= 128
        body = await reader.readexactly(value) if value else b""
        return bytes([type_flags]) + varint + body

    def _next_id(self) -> int:
        self._pkt_id = (self._pkt_id % 0xFFFF) + 1
        return self._pkt_id

    def _require_writer(self) -> asyncio.StreamWriter:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise MqttError("MQTT session not connected")
        return writer

    async def ping_loop(self) -> None:
        """Keepalive: PINGREQ every PING_EVERY_S while the session is open."""
        try:
            while not self._closed:
                await asyncio.sleep(PING_EVERY_S)
                writer = self._writer
                if writer is None or writer.is_closing():
                    break
                writer.write(_PINGREQ)
                await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def close(self) -> None:
        self._closed = True
        writer = self._writer
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.write(_DISCONNECT)
            with contextlib.suppress(Exception):
                writer.close()
        waiter = self._pub_wait
        self._pub_wait = None
        if waiter is not None and not waiter.done():
            waiter.cancel()

    async def aclose(self) -> None:
        self.close()
        writer = self._writer
        if writer is not None:
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    # Hook for inbound payloads: ``async def on_message(topic, payload)``.
    on_message: Callable[[str, bytes], Any] | None = None


# --------------------------------------------------------------------------
# Rendezvous session: MQTT topics <-> Noise record stream.
# --------------------------------------------------------------------------


def rdv_topic(sid: bytes, side: str) -> str:
    """One direction's topic. ``side`` is ``pc`` or ``phone``."""
    if len(sid) != 16:
        raise ValueError("session id must be 16 bytes")
    if side not in ("pc", "phone"):
        raise ValueError("rdv side must be 'pc' or 'phone'")
    return f"remedy/{sid.hex()}/{side}"


class RendezvousSession:
    """Bridge one MQTT rendezvous to a Noise record stream.

    The PC side: subscribes to ``phone``, publishes to ``pc``. A real
    ``(reader, writer)`` pair (socketpair-backed) is handed to the Connect
    handler exactly like a LAN client; records travel as one MQTT message each
    (MQTT supplies the framing, so the u32be prefix is stripped/re-added).
    """

    def __init__(
        self,
        mqtt: MqttSession,
        sid: bytes,
        *,
        role: str = "pc",
        timeout: float = 8.0,
    ) -> None:
        if role not in ("pc", "phone"):
            raise ValueError("role must be 'pc' or 'phone'")
        self.mqtt = mqtt
        self.sid = bytes(sid)
        self.role = role
        self.out_topic = rdv_topic(sid, role)
        self.in_topic = rdv_topic(sid, "phone" if role == "pc" else "pc")
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._sock_b: socket.socket | None = None
        self._tasks: list[asyncio.Task[Any]] = []

    async def open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        mqtt = self.mqtt
        mqtt.on_message = self._on_message
        await asyncio.wait_for(mqtt.subscribe([self.in_topic], qos=1), timeout=self.timeout)
        a, b = socket.socketpair()
        a.setblocking(False)
        b.setblocking(False)
        self._sock_b = b
        loop = asyncio.get_running_loop()
        reader, writer = await asyncio.open_connection(sock=a)
        self.reader = reader
        self.writer = writer
        self._tasks.append(loop.create_task(self._pump_out(), name="rdv-pump-out"))
        self._tasks.append(loop.create_task(mqtt._reader_loop(), name="rdv-mqtt-reader"))
        self._tasks.append(loop.create_task(mqtt.ping_loop(), name="rdv-mqtt-ping"))
        return reader, writer

    async def _on_message(self, topic: str, payload: bytes) -> None:
        if topic != self.in_topic:
            return
        sock = self._sock_b
        if sock is None or len(payload) > MAX_RECORD:
            return
        try:
            await asyncio.get_running_loop().sock_sendall(
                sock, struct.pack("!I", len(payload)) + payload
            )
        except OSError:
            pass

    async def _pump_out(self) -> None:
        """Read u32be-framed records from the peer end and publish them."""
        sock = self._sock_b
        loop = asyncio.get_running_loop()
        try:
            while sock is not None:
                header = await loop.sock_recv(sock, 4)
                if len(header) < 4:
                    break
                (length,) = struct.unpack("!I", header)
                if length > MAX_RECORD:
                    break
                payload = b""
                while len(payload) < length:
                    chunk = await loop.sock_recv(sock, length - len(payload))
                    if not chunk:
                        break
                    payload += chunk
                if len(payload) != length:
                    break
                await self.mqtt.publish(self.out_topic, payload, qos=1)
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            self.close()

    def close(self) -> None:
        writer = self.writer
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
        sock = self._sock_b
        self._sock_b = None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()
        self.mqtt.close()
        for task in self._tasks:
            if not task.done():
                task.cancel()

    async def aclose(self) -> None:
        self.close()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        await self.mqtt.aclose()


def rdv_qr_value() -> str:
    """Semicolon-separated ``host:port`` list for the pairing QR ``rdv=`` line."""
    return ";".join(f"{host}:{port}" for host, port in PUBLIC_RDV_ENDPOINTS)


def parse_rdv_endpoints(value: str) -> list[tuple[str, int]]:
    """Parse the QR ``rdv=`` value back into endpoints (fail closed)."""
    from remedy.connect.rendezvous import parse_relay_endpoint

    out: list[tuple[str, int]] = []
    for chunk in (value or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(parse_relay_endpoint(chunk))
        except ValueError:
            continue
    if not out:
        raise ValueError("rdv list is empty")
    return out
