"""Same-LAN mDNS advertise for Grove Connect.

Service: ``_remedy-connect._udp`` on the RFC 6762 group ``224.0.0.251:5353``.
TXT carries a **host-pub hash** (blake2s-256 of the 32-byte public key, first
16 hex chars). Never the local API, Bearer token, or pair secret.

No extra dependency: packets are stdlib UDP. Unit tests use the encode helpers
and do not need a live multicast path.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import socket
import struct
import threading
from collections.abc import Callable

from remedy.connect.bind import assert_chosen_bind, is_chosen_ipv4

log = logging.getLogger(__name__)

SERVICE_TYPE = "_remedy-connect._udp"
SERVICE_NAME = f"{SERVICE_TYPE}.local"
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
DNS_PTR = 12
DNS_TXT = 16
DNS_A = 1
DNS_SRV = 33
DNS_IN = 1
# QR=1, AA=1 — unicast-looking mDNS announcement.
_MDNS_RESPONSE_FLAGS = 0x8400
_TTL = 120


def host_pub_hash(host_pub: bytes) -> str:
    """blake2s-256 of the host public key, truncated to 16 hex characters."""
    digest = hashlib.blake2s(bytes(host_pub), digest_size=32).hexdigest()
    return digest[:16]


def _advertise_ipv4(bind_host: str) -> str:
    host = assert_chosen_bind(bind_host)
    if not is_chosen_ipv4(host):
        raise ValueError(f"mDNS advertise needs a chosen IPv4, not {bind_host!r}")
    return host


def encode_dns_name(name: str) -> bytes:
    """Length-prefixed DNS labels, no compression."""
    out = bytearray()
    trimmed = name.strip().rstrip(".")
    if not trimmed:
        return b"\x00"
    for label in trimmed.split("."):
        raw = label.encode("ascii")
        if len(raw) > 63:
            raise ValueError(f"DNS label too long: {label!r}")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def encode_mdns_query(service: str = SERVICE_TYPE) -> bytes:
    """DNS-SD PTR question for the Grove Connect service (no secrets)."""
    qname = service if service.endswith(".local") else f"{service}.local"
    question = encode_dns_name(qname) + struct.pack("!HH", DNS_PTR, DNS_IN)
    header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    return header + question


def _txt_rdata(entries: list[str]) -> bytes:
    out = bytearray()
    for item in entries:
        raw = item.encode("ascii")
        if len(raw) > 255:
            raise ValueError("TXT string too long")
        out.append(len(raw))
        out.extend(raw)
    return bytes(out)


def _rr(name: str, rtype: int, ttl: int, rdata: bytes) -> bytes:
    return encode_dns_name(name) + struct.pack("!HHIH", rtype, DNS_IN, ttl, len(rdata)) + rdata


def encode_mdns_announce(bind_host: str, port: int, host_pub: bytes) -> bytes:
    """Multicast DNS-SD announcement. TXT is host-pub hash only."""
    hp = host_pub_hash(host_pub)
    instance = f"Remedy-{hp}.{SERVICE_NAME}"
    target = f"remedy-{hp}.local"
    txt = _txt_rdata([f"hp={hp}"])
    srv = struct.pack("!HHH", 0, 0, int(port) & 0xFFFF) + encode_dns_name(target)
    a_rdata = socket.inet_aton(_advertise_ipv4(bind_host))
    answers = (
        _rr(SERVICE_NAME, DNS_PTR, _TTL, encode_dns_name(instance))
        + _rr(instance, DNS_SRV, _TTL, srv)
        + _rr(instance, DNS_TXT, _TTL, txt)
        + _rr(target, DNS_A, _TTL, a_rdata)
    )
    header = struct.pack("!HHHHHH", 0, _MDNS_RESPONSE_FLAGS, 0, 4, 0, 0)
    return header + answers


def start_advertiser(bind_host: str, port: int, host_pub: bytes) -> Callable[[], None]:
    """Advertise ``_remedy-connect._udp`` on the same L2. Returns a stop function.

    The packet never includes the API, Bearer, or pair secret — only a host-pub
    hash. Live send failures are swallowed so unit tests and locked-down NICs
    still get a working stop callback.
    """
    host = _advertise_ipv4(bind_host)
    packet = encode_mdns_announce(host, port, host_pub)
    stop = threading.Event()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host))
    try:
        sock.bind((host, 0))
    except OSError:
        sock.close()

        def _already_stopped() -> None:
            return None

        return _already_stopped

    def _loop() -> None:
        while not stop.is_set():
            with contextlib.suppress(OSError):
                sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
            stop.wait(1.5)
        with contextlib.suppress(OSError):
            sock.close()

    thread = threading.Thread(target=_loop, name="connect-mdns", daemon=True)
    thread.start()
    log.info("Grove Connect mDNS advertising %s on %s:%s", SERVICE_TYPE, host, port)

    def _stop() -> None:
        stop.set()
        with contextlib.suppress(OSError):
            sock.close()
        thread.join(timeout=2.0)

    return _stop
