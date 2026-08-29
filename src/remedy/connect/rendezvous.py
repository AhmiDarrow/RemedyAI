"""Rendezvous tokens and relay endpoints for Grove Connect.

The owner-run relay sees only a 16-byte session id and ciphertext frames.
It never sees ``local_api_token``, the pair secret in the clear, or Noise keys.
"""

from __future__ import annotations

import hashlib
import ipaddress
from urllib.parse import urlparse

from remedy.connect.bind import is_wildcard_bind
from remedy.connect.relay import SESSION_ID_LEN

_PAIR_PREFIX = b"remedy-connect/1|pair|"
_DEV_PREFIX = b"remedy-connect/1|dev|"


def session_id_pair(host_pub: bytes, pair_secret: bytes) -> bytes:
    """16-byte rendezvous id for the 60s pair window."""
    if len(host_pub) != 32 or len(pair_secret) != 32:
        raise ValueError("host pub and pair secret must be 32 bytes")
    return hashlib.blake2s(
        _PAIR_PREFIX + host_pub + b"|" + pair_secret, digest_size=SESSION_ID_LEN
    ).digest()


def session_id_device(host_pub: bytes, device_pub: bytes) -> bytes:
    """16-byte rendezvous id for a paired device (reconnect from any network)."""
    if len(host_pub) != 32 or len(device_pub) != 32:
        raise ValueError("host and device pubs must be 32 bytes")
    return hashlib.blake2s(
        _DEV_PREFIX + host_pub + b"|" + device_pub, digest_size=SESSION_ID_LEN
    ).digest()


def parse_relay_endpoint(url: str) -> tuple[str, int]:
    """Parse ``host:port`` or ``tcp://host:port``. No credentials, no query, no Bearer.

    The *listen* side of a relay is still a chosen IPv4. Dialing may use a
    hostname so a phone on LTE can reach an owner VPS.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("relay URL is empty")
    low = raw.lower()
    if "local_api_token" in low or "bearer " in low or "authorization=" in low:
        raise ValueError("relay URL must not carry secrets")
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme in ("http", "https"):
            raise ValueError("relay is a TCP splice, not HTTP")
        if parsed.scheme and parsed.scheme not in ("tcp", "relay"):
            raise ValueError(f"unsupported relay scheme {parsed.scheme!r}")
        if parsed.username or parsed.password:
            raise ValueError("relay URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("relay URL must not contain a query")
        host = (parsed.hostname or "").strip()
        port = int(parsed.port or 7402)
    else:
        host, port = _split_host_port(raw)
    if not host:
        raise ValueError("relay host is empty")
    if is_wildcard_bind(host):
        raise ValueError("relay must not be a wildcard bind")
    if port <= 0 or port > 65535:
        raise ValueError("relay port out of range")
    host = host.strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_unspecified or ip.is_multicast:
            raise ValueError("relay address is not a unicast host")
    else:
        if not host or any(c in host for c in "/ \\") or ".." in host:
            raise ValueError("relay host is not a hostname")
    return host, port


def _split_host_port(raw: str) -> tuple[str, int]:
    text = raw.strip()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ValueError("relay IPv6 address is missing ']'")
        host = text[1:end]
        rest = text[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") else 7402
        return host, port
    if text.count(":") == 1:
        host, _, port_s = text.partition(":")
        return host, int(port_s or 7402)
    if ":" in text and text.count(":") > 1:
        # bare IPv6 without port
        return text, 7402
    return text, 7402
