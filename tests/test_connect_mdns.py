"""Grove Connect mDNS advertise — service name only, no secrets."""

from __future__ import annotations

import asyncio

import pytest

from remedy.connect.mdns import (
    SERVICE_TYPE,
    encode_mdns_announce,
    encode_mdns_query,
    host_pub_hash,
    start_advertiser,
)

HOST_PUB = bytes(range(32))
PAIR_SECRET = "pair-secret-super-secret-do-not-leak"
BIND_HOST = "192.168.1.20"


def test_host_pub_hash_is_16_hex_chars() -> None:
    hp = host_pub_hash(HOST_PUB)
    assert len(hp) == 16
    assert hp == host_pub_hash(HOST_PUB)
    int(hp, 16)  # hex
    assert host_pub_hash(b"\x00" * 32) != hp


def test_mdns_query_contains_service_name() -> None:
    pkt = encode_mdns_query()
    assert b"_remedy-connect" in pkt
    assert b"_udp" in pkt
    assert SERVICE_TYPE.encode() in pkt or b"_remedy-connect" in pkt
    assert b"Bearer" not in pkt
    assert b"local_api_token" not in pkt


def test_mdns_announce_contains_service_and_hash_not_secrets() -> None:
    pkt = encode_mdns_announce(BIND_HOST, 7401, HOST_PUB)
    assert b"_remedy-connect" in pkt
    assert b"_udp" in pkt
    hp = host_pub_hash(HOST_PUB).encode("ascii")
    assert hp in pkt
    assert HOST_PUB not in pkt
    assert b"Bearer" not in pkt
    assert b"local_api_token" not in pkt
    assert PAIR_SECRET.encode() not in pkt
    assert b"pair-secret" not in pkt


def test_pair_secret_not_in_mdns() -> None:
    """Encoder has no pair-secret parameter; a secret string must not appear."""
    pkt = encode_mdns_announce(BIND_HOST, 7401, HOST_PUB)
    assert PAIR_SECRET.encode("ascii") not in pkt
    assert b"Bearer" not in pkt
    assert b"local_api_token" not in pkt
    # Even stuffing ASCII into the public key still only advertises the hash.
    sneaky = b"Bearer local_api_token" + b"\x00" * 10
    sneaky_pkt = encode_mdns_announce(BIND_HOST, 7401, sneaky)
    assert b"Bearer" not in sneaky_pkt
    assert b"local_api_token" not in sneaky_pkt
    assert host_pub_hash(sneaky).encode("ascii") in sneaky_pkt


def test_start_advertiser_returns_stop_without_live_multicast() -> None:
    stop = start_advertiser("127.0.0.1", 7401, HOST_PUB)
    try:
        assert callable(stop)
    finally:
        stop()
        stop()  # idempotent


@pytest.fixture
def _connect_home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


def test_start_connect_server_starts_and_stops_advertiser(_connect_home, monkeypatch) -> None:
    """start_connect_server must call start_advertiser with the bound addr + host pub."""
    calls: list[tuple[str, int, bytes]] = []
    stops = {"n": 0}

    def fake_start(bind_host: str, port: int, host_pub: bytes):
        calls.append((str(bind_host), int(port), bytes(host_pub)))

        def _stop() -> None:
            stops["n"] += 1

        return _stop

    monkeypatch.setattr("remedy.connect.mdns.start_advertiser", fake_start)

    from remedy.connect.keys import load_or_create_host_keypair
    from remedy.connect.server import listening_addr, start_connect_server, stop_connect_server

    async def _run() -> None:
        await start_connect_server("127.0.0.1", 0, sidecar_port=7400, api_key="k")
        try:
            addr = listening_addr()
            assert addr is not None
            assert calls, "start_advertiser was never called from start_connect_server"
            host, port, pub = calls[0]
            assert host == addr[0]
            assert port == addr[1]
            assert pub == bytes(load_or_create_host_keypair().public)
            assert len(pub) == 32
        finally:
            await stop_connect_server()
        assert stops["n"] >= 1
        assert listening_addr() is None

    asyncio.run(_run())


def test_advertiser_failure_does_not_fake_multicast(_connect_home, monkeypatch) -> None:
    """No zeroconf/extra deps: if advertise fails, fail closed — still bind, no fake packet."""

    def boom(*_a, **_k):
        raise OSError("multicast not available")

    monkeypatch.setattr("remedy.connect.mdns.start_advertiser", boom)

    from remedy.connect.server import listening_addr, start_connect_server, stop_connect_server

    async def _run() -> None:
        await start_connect_server("127.0.0.1", 0, sidecar_port=7400, api_key="k")
        try:
            addr = listening_addr()
            assert addr is not None
            assert addr[0] == "127.0.0.1"
            assert int(addr[1]) > 0
        finally:
            await stop_connect_server()
        assert listening_addr() is None

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_start_connect_server_wires_mdns(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    called: dict = {}

    def fake_adv(host, port, pub):
        called["host"] = host
        called["port"] = port
        called["pub"] = pub

        def _stop() -> None:
            called["stopped"] = True

        return _stop

    monkeypatch.setattr("remedy.connect.mdns.start_advertiser", fake_adv)
    from remedy.connect.server import start_connect_server, stop_connect_server

    await start_connect_server(
        "127.0.0.1",
        0,
        sidecar_port=7400,
        api_key="t",
        config={},
    )
    try:
        assert called.get("host") == "127.0.0.1"
        assert int(called.get("port") or 0) > 0
        assert isinstance(called.get("pub"), (bytes, bytearray))
        assert len(called["pub"]) == 32
    finally:
        await stop_connect_server()
    assert called.get("stopped") is True
