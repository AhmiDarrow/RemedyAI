"""Public STUN (RFC 5389) + a one-byte UDP punch helper.

No account. Binding-request encode/decode is real enough for tests to parse a
crafted success. Simultaneous Noise-over-UDP is out of scope (the Connect pipe
is TCP); ``udp_punch_once`` only sends a 1-byte probe.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
from typing import Any

MAGIC_COOKIE = 0x2112A442
MAGIC_COOKIE_BYTES = MAGIC_COOKIE.to_bytes(4, "big")
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
ATTR_MAPPED_ADDRESS = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020
FAMILY_IPV4 = 0x01
FAMILY_IPV6 = 0x02
_HEADER_LEN = 20


def is_ipv6_literal(host: str) -> bool:
    """True when ``host`` is an IPv6 address (optional brackets)."""
    raw = (host or "").strip().strip("[]")
    try:
        return ipaddress.ip_address(raw).version == 6
    except ValueError:
        return False


def wrap_ipv6_host(host: str) -> str:
    """Bracket an IPv6 literal for host:port pairing; leave IPv4 alone."""
    h = (host or "").strip()
    if not h:
        return h
    if h.startswith("[") and h.endswith("]"):
        return h
    if is_ipv6_literal(h):
        return f"[{h}]"
    return h


def family_for_host(host: str) -> int:
    """``socket.AF_INET6`` for IPv6 literals, else ``AF_INET``."""
    return socket.AF_INET6 if is_ipv6_literal(host) else socket.AF_INET


def sockaddr(host: str, port: int) -> tuple[Any, ...]:
    """Address tuple suitable for bind/sendto (IPv6 includes flowinfo/scope)."""
    h = (host or "").strip().strip("[]")
    if is_ipv6_literal(h):
        return (h, int(port), 0, 0)
    return (h, int(port))


def encode_binding_request(transaction_id: bytes | None = None) -> bytes:
    """RFC 5389 Binding request (20-byte header, no attributes)."""
    txid = os.urandom(12) if transaction_id is None else bytes(transaction_id)
    if len(txid) != 12:
        raise ValueError("STUN transaction id must be 12 bytes")
    return struct.pack("!HHI", BINDING_REQUEST, 0, MAGIC_COOKIE) + txid


def _xor_port(port: int) -> int:
    return port ^ (MAGIC_COOKIE >> 16)


def _xor_ipv4(packed: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(packed, MAGIC_COOKIE_BYTES, strict=True))


def _xor_ipv6(packed: bytes, txid: bytes) -> bytes:
    mask = MAGIC_COOKIE_BYTES + txid
    return bytes(a ^ b for a, b in zip(packed, mask, strict=True))


def encode_xor_mapped_address(
    host: str,
    port: int,
    transaction_id: bytes,
    *,
    xor: bool = True,
) -> bytes:
    """MAPPED-ADDRESS or XOR-MAPPED-ADDRESS attribute body + header."""
    ip = ipaddress.ip_address(host.strip().strip("[]"))
    family = FAMILY_IPV6 if ip.version == 6 else FAMILY_IPV4
    packed = ip.packed
    xport = _xor_port(port) if xor else int(port) & 0xFFFF
    xaddr = packed
    if xor:
        xaddr = _xor_ipv6(packed, transaction_id) if ip.version == 6 else _xor_ipv4(packed)
    body = struct.pack("!BBH", 0, family, xport & 0xFFFF) + xaddr
    atype = ATTR_XOR_MAPPED_ADDRESS if xor else ATTR_MAPPED_ADDRESS
    pad = (4 - (len(body) % 4)) % 4
    return struct.pack("!HH", atype, len(body)) + body + (b"\x00" * pad)


def encode_binding_success(
    host: str,
    port: int,
    *,
    transaction_id: bytes | None = None,
    xor: bool = True,
) -> bytes:
    """Craft a Binding success with a (XOR-)MAPPED-ADDRESS. Tests use this too."""
    txid = os.urandom(12) if transaction_id is None else bytes(transaction_id)
    if len(txid) != 12:
        raise ValueError("STUN transaction id must be 12 bytes")
    attr = encode_xor_mapped_address(host, port, txid, xor=xor)
    header = struct.pack("!HHI", BINDING_SUCCESS, len(attr), MAGIC_COOKIE) + txid
    return header + attr


def _parse_one_address(attr_type: int, value: bytes, txid: bytes) -> tuple[str, int] | None:
    if len(value) < 4:
        return None
    _reserved, family, port = struct.unpack("!BBH", value[:4])
    xor = attr_type == ATTR_XOR_MAPPED_ADDRESS
    if xor:
        port = _xor_port(port)
    if family == FAMILY_IPV4:
        if len(value) < 8:
            return None
        packed = value[4:8]
        if xor:
            packed = _xor_ipv4(packed)
        return (str(ipaddress.IPv4Address(packed)), int(port))
    if family == FAMILY_IPV6:
        if len(value) < 20:
            return None
        packed = value[4:20]
        if xor:
            packed = _xor_ipv6(packed, txid)
        return (str(ipaddress.IPv6Address(packed)), int(port))
    return None


def parse_mapped_address(response: bytes) -> tuple[str, int] | None:
    """Return ``(ip, port)`` from XOR-MAPPED-ADDRESS or MAPPED-ADDRESS, else None."""
    data = bytes(response)
    if len(data) < _HEADER_LEN:
        return None
    _msg_type, length, cookie = struct.unpack("!HHI", data[:8])
    if cookie != MAGIC_COOKIE:
        return None
    txid = data[8:_HEADER_LEN]
    attrs = data[_HEADER_LEN : _HEADER_LEN + length]
    xor_hit: tuple[str, int] | None = None
    mapped_hit: tuple[str, int] | None = None
    offset = 0
    while offset + 4 <= len(attrs):
        atype, alen = struct.unpack("!HH", attrs[offset : offset + 4])
        offset += 4
        if offset + alen > len(attrs):
            break
        value = attrs[offset : offset + alen]
        offset += alen
        offset += (4 - (alen % 4)) % 4
        if atype == ATTR_XOR_MAPPED_ADDRESS:
            xor_hit = _parse_one_address(atype, value, txid) or xor_hit
        elif atype == ATTR_MAPPED_ADDRESS:
            mapped_hit = _parse_one_address(atype, value, txid) or mapped_hit
    return xor_hit or mapped_hit


def udp_punch_once(
    local_addr: tuple[str, int],
    peer_addr: tuple[str, int],
    *,
    sock: socket.socket | None = None,
) -> int:
    """Send a 1-byte UDP probe from ``local_addr`` toward ``peer_addr``.

    Tests may inject ``sock`` (no live packet). The Connect pipe is TCP; this
    is only a reachability stub, not a Noise transport.
    """
    payload = b"\x00"
    owned = sock is None
    if sock is None:
        family = family_for_host(local_addr[0])
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.bind(sockaddr(local_addr[0], local_addr[1]))
        except OSError:
            sock.close()
            raise
    try:
        dest = sockaddr(peer_addr[0], peer_addr[1])
        return int(sock.sendto(payload, dest))
    finally:
        if owned:
            sock.close()
