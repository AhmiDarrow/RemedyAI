"""STUN Binding parse (RFC 5389) and UDP punch stub."""

from __future__ import annotations

import ipaddress
import struct
from unittest.mock import MagicMock

from remedy.connect.stun import (
    BINDING_SUCCESS,
    MAGIC_COOKIE,
    encode_binding_request,
    encode_binding_success,
    family_for_host,
    is_ipv6_literal,
    parse_mapped_address,
    udp_punch_once,
    wrap_ipv6_host,
)


def test_encode_binding_request_has_magic_cookie() -> None:
    txid = bytes(range(12))
    pkt = encode_binding_request(txid)
    assert len(pkt) == 20
    _typ, length, cookie = struct.unpack("!HHI", pkt[:8])
    assert cookie == MAGIC_COOKIE
    assert length == 0
    assert pkt[8:] == txid


def test_parse_mapped_address_crafted_xor_ipv4() -> None:
    txid = bytes(range(12))
    ip = "203.0.113.10"
    port = 3478
    packed = ipaddress.IPv4Address(ip).packed
    xport = port ^ (MAGIC_COOKIE >> 16)
    xip = bytes(a ^ b for a, b in zip(packed, MAGIC_COOKIE.to_bytes(4, "big"), strict=True))
    attr = struct.pack("!HHBBH", 0x0020, 8, 0, 0x01, xport) + xip
    header = struct.pack("!HHI", BINDING_SUCCESS, len(attr), MAGIC_COOKIE) + txid
    pkt = header + attr
    assert parse_mapped_address(pkt) == (ip, port)


def test_parse_mapped_address_crafted_plain_ipv4() -> None:
    txid = b"\xab" * 12
    ip = "192.0.2.55"
    port = 12345
    packed = ipaddress.IPv4Address(ip).packed
    attr = struct.pack("!HHBBH", 0x0001, 8, 0, 0x01, port) + packed
    header = struct.pack("!HHI", BINDING_SUCCESS, len(attr), MAGIC_COOKIE) + txid
    assert parse_mapped_address(header + attr) == (ip, port)


def test_parse_mapped_address_xor_ipv6() -> None:
    txid = bytes(range(12))
    ip = "2001:db8::1"
    port = 5353
    pkt = encode_binding_success(ip, port, transaction_id=txid, xor=True)
    got = parse_mapped_address(pkt)
    assert got is not None
    assert ipaddress.IPv6Address(got[0]) == ipaddress.IPv6Address(ip)
    assert got[1] == port


def test_parse_mapped_address_garbage_is_none() -> None:
    assert parse_mapped_address(b"") is None
    assert parse_mapped_address(b"\x00" * 20) is None
    assert parse_mapped_address(b"not-stun") is None


def test_ipv6_helpers() -> None:
    assert is_ipv6_literal("2001:db8::1")
    assert is_ipv6_literal("[::1]")
    assert not is_ipv6_literal("127.0.0.1")
    assert wrap_ipv6_host("2001:db8::1") == "[2001:db8::1]"
    assert wrap_ipv6_host("127.0.0.1") == "127.0.0.1"
    assert family_for_host("::1") != family_for_host("127.0.0.1")


def test_udp_punch_once_sends_one_byte() -> None:
    sock = MagicMock()
    sock.sendto.return_value = 1
    n = udp_punch_once(("127.0.0.1", 40000), ("203.0.113.9", 40001), sock=sock)
    assert n == 1
    (payload, dest), _kwargs = sock.sendto.call_args
    assert payload == b"\x00"
    assert dest[0] == "203.0.113.9"
    assert dest[1] == 40001
    sock.close.assert_not_called()


def test_udp_punch_once_ipv6_dest() -> None:
    sock = MagicMock()
    sock.sendto.return_value = 1
    udp_punch_once(("::1", 40000), ("2001:db8::9", 40001), sock=sock)
    (_payload, dest), _kwargs = sock.sendto.call_args
    assert dest[0] == "2001:db8::9"
    assert dest[1] == 40001
