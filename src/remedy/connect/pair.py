"""One-shot pairing for Grove Connect.

QR text carries the host static public and a 60s pair secret. It never includes
``local_api_token`` or a Bearer. ``hp`` / ``ps`` are URL-safe base64 without
padding.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from remedy.connect.store import (
    MAX_DEVICES,
    active_device_count,
    find_device_by_public,
    save_device,
)
from remedy.core.security import secret_equals

logger = logging.getLogger(__name__)

PAIR_TTL_S = 60
PAIR_SECRET_LEN = 32
QR_VERSION = "remedy-connect/1"

_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    raw = (text or "").strip()
    pad = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _kp_public(kp: object) -> bytes:
    if isinstance(kp, (bytes, bytearray)) and len(kp) == 32:
        return bytes(kp)
    for attr in ("public", "public_bytes", "pk", "public_key"):
        value = getattr(kp, attr, None)
        if value is None:
            continue
        if callable(value):
            try:
                value = value()
            except TypeError:
                try:
                    value = value(raw=True)
                except TypeError:
                    continue
        if isinstance(value, (bytes, bytearray)) and len(value) == 32:
            return bytes(value)
        encode = getattr(value, "encode", None)
        if callable(encode):
            try:
                encoded = encode()
            except TypeError:
                encoded = None
            if isinstance(encoded, (bytes, bytearray)) and len(encoded) == 32:
                return bytes(encoded)
        try:
            as_bytes = bytes(value)
        except (TypeError, ValueError):
            as_bytes = b""
        if len(as_bytes) == 32:
            return as_bytes
    raise TypeError("host keypair has no 32-byte public")


def _host_public_bytes() -> bytes:
    from remedy.connect.keys import load_or_create_host_keypair

    return _kp_public(load_or_create_host_keypair())


@dataclass
class _Pending:
    secret: bytearray
    exp: float
    used: bool = False


_pending: _Pending | None = None


def _wipe_pending() -> None:
    global _pending
    if _pending is None:
        return
    for i in range(len(_pending.secret)):
        _pending.secret[i] = 0
    _pending.used = True
    _pending = None


def pending_pair_rendezvous() -> bytes | None:
    """16-byte relay session id for the live QR window, or None."""
    with _lock:
        if _pending is None or _pending.used:
            return None
        if _now() > _pending.exp:
            return None
        secret = bytes(_pending.secret)
    try:
        from remedy.connect.rendezvous import session_id_pair

        return session_id_pair(_host_public_bytes(), secret)
    except Exception:
        return None


def pending_secret_for_test() -> bytes | None:
    """Test helper: copy of the live pair secret (never log this)."""
    with _lock:
        if _pending is None or _pending.used:
            return None
        return bytes(_pending.secret)


def start_pair(
    *,
    loopback: bool,
    bind_host: str,
    bind_port: int,
    v6: str = "",
    relay: str = "",
) -> str:
    """Mint a 60s one-use pair QR. Raises ``PermissionError`` off loopback."""
    if not loopback:
        raise PermissionError("pair start is loopback-only")
    host = str(bind_host or "").strip()
    port = int(bind_port or 7401)
    if not host:
        raise ValueError("bind_host required")
    if port <= 0 or port > 65535:
        raise ValueError("bind_port out of range")
    pub = _host_public_bytes()
    secret = bytearray(secrets.token_bytes(PAIR_SECRET_LEN))
    exp = int(_now() + PAIR_TTL_S)
    with _lock:
        global _pending
        _wipe_pending()
        _pending = _Pending(secret=secret, exp=float(exp), used=False)
    lines = [
        QR_VERSION,
        f"hp={_b64u(pub)}",
        f"ps={_b64u(bytes(secret))}",
        f"lan={host}:{port}",
    ]
    v6_s = str(v6 or "").strip()
    if v6_s:
        lines.append(f"v6={v6_s}")
    relay_s = str(relay or "").strip()
    if relay_s:
        from remedy.connect.rendezvous import parse_relay_endpoint

        host_r, port_r = parse_relay_endpoint(relay_s)
        lines.append(f"relay={host_r}:{port_r}")
    lines.append(f"exp={exp}")
    text = "\n".join(lines)
    # Belt: never emit the local API token shape even if a caller stuffed it
    # into bind_host (which we don't).
    lowered = text.lower()
    if "local_api_token" in lowered or "bearer " in lowered:
        _wipe_pending()
        raise RuntimeError("refusing to emit a QR that mentions local auth")
    return text


def parse_pair_secret(qr_text: str) -> bytes:
    """Decode the ``ps=`` field from QR text (URL-safe base64)."""
    for line in (qr_text or "").splitlines():
        if line.startswith("ps="):
            return b64u_decode(line[3:].strip())
    raise ValueError("QR missing ps")


def _device_id_for(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:32]


def complete_pair(secret: bytes | str | bytearray, device_pub: bytes, name: str) -> str:
    """Consume the one-use secret and persist the device. Fail closed."""
    presented = bytes(secret) if not isinstance(secret, str) else b64u_decode(secret)
    pub = bytes(device_pub or b"")
    if len(pub) != 32:
        raise ValueError("device public key must be 32 bytes")
    label = str(name or "phone").strip()[:80] or "phone"
    with _lock:
        pending = _pending
        if pending is None or pending.used:
            raise ValueError("reused")
        if _now() > pending.exp:
            _wipe_pending()
            raise ValueError("expired")
        expected = bytes(pending.secret)
        ok = secret_equals(presented, expected)
        if not ok:
            raise ValueError("invalid")
        existing = find_device_by_public(pub.hex())
        if existing is not None and not existing.get("revoked"):
            device_id = str(existing["id"])
            existing["name"] = label
            existing["paired_at"] = _now()
            save_device(existing)
            _wipe_pending()
            return device_id
        if active_device_count() >= MAX_DEVICES:
            _wipe_pending()
            raise ValueError("device limit")
        device_id = _device_id_for(pub)
        rec = {
            "id": device_id,
            "name": label,
            "public_hex": pub.hex(),
            "paired_at": _now(),
            "revoked": False,
        }
        save_device(rec)
        _wipe_pending()
    try:
        from remedy.connect.audit import append_event

        append_event("pair", device_id=device_id, name=label)
    except Exception:
        pass
    return device_id


def pair_payload(secret: bytes, name: str) -> bytes:
    """Handshake payload for an unpaired phone (initiator)."""
    label = str(name or "phone").encode("utf-8")[:80]
    return b"pair\0" + bytes(secret) + b"\0" + label


def hello_payload(device_id: str) -> bytes:
    return b"hello\0" + str(device_id).encode("utf-8")


def parse_handshake_payload(payload: bytes) -> tuple[str, dict[str, Any]]:
    """Return ``('pair'|'hello', fields)``. Fail closed on garbage.

    Phone (Android) first-message payload is the raw 32-byte pair secret.
    Python tests / later sessions may send ``pair\\0secret\\0name`` or ``hello\\0id``.
    """
    data = bytes(payload or b"")
    if len(data) == PAIR_SECRET_LEN:
        return "pair", {"secret": data, "name": "phone"}
    if data.startswith(b"pair\0"):
        rest = data[5:]
        # Secret is raw 32 bytes and may contain NUL; do not partition on NUL.
        if len(rest) < PAIR_SECRET_LEN + 1 or rest[PAIR_SECRET_LEN : PAIR_SECRET_LEN + 1] != b"\0":
            raise ValueError("invalid pair payload")
        secret = rest[:PAIR_SECRET_LEN]
        name = rest[PAIR_SECRET_LEN + 1 :].decode("utf-8", errors="replace")
        return "pair", {"secret": secret, "name": name}
    if data.startswith(b"hello\0"):
        device_id = data[6:].decode("utf-8", errors="replace").strip()
        if not device_id:
            raise ValueError("invalid hello payload")
        return "hello", {"device_id": device_id}
    raise ValueError("unknown handshake payload")
