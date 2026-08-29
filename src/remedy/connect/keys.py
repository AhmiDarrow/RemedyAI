"""Host static X25519 key at ``~/.remedy/auth/connect/host.key``.

Windows: DPAPI envelope ``{v: 2, dpapi: true, p: <base64>}``.
POSIX: 0o600 JSON ``{v: 2, encoding: "plain", p: <base64>}`` or raw 32 bytes.

Never logs the private key.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from remedy.connect.noise import DHLEN, KeyPair
from remedy.core.atomic_json import write_bytes_atomic
from remedy.interfaces.secret_store import (
    _dpapi_available,
    _dpapi_protect,
    _dpapi_unprotect,
    _harden_path,
    auth_dir,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
HOST_KEY_NAME = "host.key"


def host_key_path(home: Path | None = None) -> Path:
    return auth_dir(home) / "connect" / HOST_KEY_NAME


def load_or_create_host_keypair(home: Path | None = None) -> KeyPair:
    """Return the host static key, creating it once if missing."""
    path = host_key_path(home)
    with _lock:
        if path.is_file() and path.stat().st_size > 0:
            return _load_host_keypair(path)
        kp = KeyPair.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        _harden_path(path.parent, is_dir=True)
        write_bytes_atomic(path, _encode_host_key(kp), mode=0o600)
        _harden_path(path, is_dir=False)
        logger.info("created host connect key at %s", path)
        return _load_host_keypair(path)


def _encode_host_key(kp: KeyPair) -> bytes:
    if _dpapi_available():
        try:
            sealed = _dpapi_protect(kp.private)
            envelope: dict[str, Any] = {
                "v": 2,
                "dpapi": True,
                "p": base64.b64encode(sealed).decode("ascii"),
            }
            return (json.dumps(envelope, indent=2) + "\n").encode("utf-8")
        except Exception as exc:
            logger.warning(
                "host key DPAPI protect failed (%s); storing owner-only",
                type(exc).__name__,
            )
    envelope = {
        "v": 2,
        "encoding": "plain",
        "p": base64.b64encode(kp.private).decode("ascii"),
    }
    return (json.dumps(envelope, indent=2) + "\n").encode("utf-8")


def _load_host_keypair(path: Path) -> KeyPair:
    raw = path.read_bytes()
    sk = _decode_host_key(raw)
    return KeyPair.from_private(sk)


def _decode_host_key(raw: bytes) -> bytes:
    if len(raw) == DHLEN:
        return raw
    try:
        text = raw.decode("utf-8")
        outer = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("host key file is not valid JSON or raw key") from exc
    if not isinstance(outer, dict):
        raise ValueError("host key file is not a JSON object")
    sk = _sk_from_envelope(outer)
    if len(sk) != DHLEN:
        raise ValueError("host key is not a 32-byte X25519 scalar")
    return sk


def _sk_from_envelope(outer: dict[str, Any]) -> bytes:
    if outer.get("v") == 2 and outer.get("dpapi") is True:
        blob = outer.get("p") or ""
        if sys.platform != "win32":
            raise ValueError("DPAPI host key cannot be read on this platform")
        return _dpapi_unprotect(base64.b64decode(str(blob)))
    encoding = str(outer.get("encoding") or "")
    if encoding in ("plain", "raw", ""):
        blob = outer.get("p") or outer.get("sk") or ""
        if blob:
            return base64.b64decode(str(blob))
    raise ValueError("unrecognized host key envelope")
