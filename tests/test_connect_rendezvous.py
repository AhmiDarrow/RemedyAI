"""Relay endpoint parsing and 16-byte rendezvous ids. No secrets on the wire token."""

from __future__ import annotations

import pytest

from remedy.connect.rendezvous import (
    parse_relay_endpoint,
    session_id_device,
    session_id_pair,
)


def test_parse_host_port():
    assert parse_relay_endpoint("192.0.2.9:7402") == ("192.0.2.9", 7402)
    assert parse_relay_endpoint("tcp://192.0.2.9:7402") == ("192.0.2.9", 7402)
    assert parse_relay_endpoint("relay.example.com:7402") == ("relay.example.com", 7402)


def test_parse_refuses_http_credentials_query_wildcard():
    with pytest.raises(ValueError, match="HTTP"):
        parse_relay_endpoint("https://example.com/relay")
    with pytest.raises(ValueError, match="credentials"):
        parse_relay_endpoint("tcp://user:pass@192.0.2.9:7402")
    with pytest.raises(ValueError, match="query"):
        parse_relay_endpoint("tcp://192.0.2.9:7402?token=1")
    with pytest.raises(ValueError, match="wildcard"):
        parse_relay_endpoint("0.0.0.0:7402")
    with pytest.raises(ValueError, match="secrets"):
        parse_relay_endpoint("192.0.2.9:7402/Bearer abc")


def test_session_ids_are_16_bytes_and_distinct():
    hp = b"\x11" * 32
    ps = b"\x22" * 32
    dp = b"\x33" * 32
    a = session_id_pair(hp, ps)
    b = session_id_device(hp, dp)
    assert len(a) == 16 and len(b) == 16
    assert a != b
    assert a == session_id_pair(hp, ps)
    assert b"\x22" * 4 not in a  # pair secret is hashed, not inlined


def test_qr_carries_relay_not_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.connect.pair import start_pair

    qr = start_pair(
        loopback=True,
        bind_host="127.0.0.1",
        bind_port=7401,
        relay="192.0.2.9:7402",
    )
    assert "relay=192.0.2.9:7402" in qr
    assert "local_api_token" not in qr.lower()
    assert "bearer" not in qr.lower()


def test_global_ipv6_helper():
    from remedy.connect.bind import is_global_ipv6

    assert is_global_ipv6("2606:4700:4700::1111") is True
    assert is_global_ipv6("fe80::1") is False
    assert is_global_ipv6("::1") is False
    assert is_global_ipv6("::") is False
