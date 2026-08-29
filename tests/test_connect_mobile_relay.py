"""Phone on another network meets the PC through the owner-run relay."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct

import pytest

from remedy.connect.lifecycle import maybe_start_connect, stop_connect
from remedy.connect.relay import start_relay


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    yield tmp_path
    stop_connect()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.mark.asyncio
async def test_pair_over_owner_relay(home):
    from remedy.connect.keys import load_or_create_host_keypair
    from remedy.connect.noise import HandshakeState, KeyPair
    from remedy.connect.pair import parse_pair_secret, pending_pair_rendezvous, start_pair
    from remedy.connect.record import decrypt_record, encrypt_record, pack_record, read_record

    relay_port, stop_relay = start_relay("127.0.0.1", 0, wait_peer_s=30.0)
    lan_port = _free_port()
    try:
        maybe_start_connect(
            None,
            {
                "connect_enabled": True,
                "connect_bind_host": "127.0.0.1",
                "connect_bind_port": lan_port,
                "connect_relay_url": f"127.0.0.1:{relay_port}",
            },
            api_key="tok-connect-test-not-a-secret",
            sidecar_port=7400,
        )
        host_kp = load_or_create_host_keypair()
        qr = start_pair(
            loopback=True,
            bind_host="127.0.0.1",
            bind_port=lan_port,
            relay=f"127.0.0.1:{relay_port}",
        )
        assert f"relay=127.0.0.1:{relay_port}" in qr
        sid = pending_pair_rendezvous()
        assert sid and len(sid) == 16

        # Host supervisor scans every 1s; give it a moment to dial the pair slot.
        await asyncio.sleep(1.6)

        reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
        writer.write(sid)
        await writer.drain()
        secret = parse_pair_secret(qr)
        device_kp = KeyPair.generate()
        hs = HandshakeState(initiator=True, s=device_kp, rs=host_kp.public)
        from remedy.connect.pair import pair_payload

        msg1 = hs.write_message(pair_payload(secret, "lte-phone"))
        writer.write(struct.pack("!I", len(msg1)) + msg1)
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(4), timeout=8)
        (n,) = struct.unpack("!I", header)
        msg2 = await reader.readexactly(n)
        hs.read_message(msg2)
        send, recv = hs.split()
        req = b"GET /connect/me HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
        writer.write(encrypt_record(send, req))
        await writer.drain()
        nonce12, ct = await asyncio.wait_for(read_record(reader), timeout=8)
        plain = decrypt_record(recv, pack_record(nonce12, ct))
        assert b"200" in plain
        assert b"panes" in plain
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        stop_relay()
        stop_connect()
