"""Paired-device store and pause flag under ``~/.remedy/auth/connect``.

Device records are DPAPI-sealed on Windows and mode ``0600`` elsewhere.
Honors ``REMEDY_HOME``.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.home import default_home

logger = logging.getLogger(__name__)

DEVICE_ID_RE = re.compile(r"^[a-f0-9]{16,64}$")
MAX_DEVICES = 3

_lock = threading.RLock()


def connect_root(home: Path | str | None = None) -> Path:
    base = Path(home) if home is not None else default_home()
    d = base / "auth" / "connect"
    d.mkdir(parents=True, exist_ok=True)
    _harden(d, is_dir=True)
    return d


def devices_dir(home: Path | str | None = None) -> Path:
    d = connect_root(home) / "devices"
    d.mkdir(parents=True, exist_ok=True)
    _harden(d, is_dir=True)
    return d


def state_path(home: Path | str | None = None) -> Path:
    return connect_root(home) / "state.json"


def _harden(path: Path, *, is_dir: bool) -> None:
    with contextlib.suppress(OSError):
        path.chmod(0o700 if is_dir else 0o600)
    try:
        from remedy.interfaces.secret_store import _harden_path

        _harden_path(path, is_dir=is_dir)
    except Exception:
        pass


def _write_sealed_json(path: Path, payload: dict[str, Any]) -> None:
    plain = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    envelope: dict[str, Any] = {
        "v": 2,
        "encoding": "plain",
        "payload": payload,
    }
    try:
        from remedy.interfaces.secret_store import _dpapi_available, _dpapi_protect

        if _dpapi_available():
            sealed = _dpapi_protect(plain)
            envelope = {
                "v": 2,
                "encoding": "dpapi",
                "dpapi": base64.b64encode(sealed).decode("ascii"),
            }
    except Exception as exc:
        logger.warning("connect DPAPI protect failed; storing owner-only plain: %s", exc)
    write_json_atomic(path, envelope, mode=0o600)
    _harden(path, is_dir=False)


def _read_sealed_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    try:
        outer = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(outer, dict):
        return None
    encoding = str(outer.get("encoding") or "").strip().lower()
    if encoding == "dpapi" or outer.get("dpapi"):
        blob = outer.get("dpapi") or outer.get("payload") or ""
        try:
            from remedy.interfaces.secret_store import _dpapi_unprotect

            plain = _dpapi_unprotect(base64.b64decode(str(blob)))
            inner = json.loads(plain.decode("utf-8"))
        except Exception as exc:
            logger.warning("connect DPAPI unprotect failed: %s", exc)
            return None
        return inner if isinstance(inner, dict) else None
    payload = outer.get("payload")
    if isinstance(payload, dict):
        return payload
    # Legacy / unwrapped device document.
    if "id" in outer or "public_hex" in outer:
        return outer
    return None


def _device_path(device_id: str, home: Path | str | None = None) -> Path:
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise ValueError("invalid device id")
    return devices_dir(home) / f"{device_id}.json"


def save_device(record: dict[str, Any], home: Path | str | None = None) -> dict[str, Any]:
    device_id = str(record.get("id") or "").strip().lower()
    if not DEVICE_ID_RE.fullmatch(device_id):
        raise ValueError("invalid device id")
    clean = {
        "id": device_id,
        "name": str(record.get("name") or "phone")[:80],
        "public_hex": str(record.get("public_hex") or "").strip().lower(),
        "paired_at": float(record.get("paired_at") or time.time()),
        "revoked": bool(record.get("revoked", False)),
    }
    with _lock:
        _write_sealed_json(_device_path(device_id, home), clean)
    if clean["revoked"]:
        _revoked_live.add(device_id)
    else:
        _revoked_live.discard(device_id)
    return clean


# Device ids revoked in this process. The gateway checks this before every
# record so a live session ends at the next frame, without a disk read.
_revoked_live: set[str] = set()


def is_revoked_live(device_id: str) -> bool:
    return str(device_id or "").strip().lower() in _revoked_live


def get_device(device_id: str, home: Path | str | None = None) -> dict[str, Any] | None:
    try:
        path = _device_path(str(device_id).strip().lower(), home)
    except ValueError:
        return None
    with _lock:
        if not path.is_file():
            return None
        return _read_sealed_json(path)


def list_devices(home: Path | str | None = None, *, include_revoked: bool = True) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with _lock:
        root = devices_dir(home)
        for path in sorted(root.glob("*.json")):
            rec = _read_sealed_json(path)
            if not rec:
                continue
            if not include_revoked and rec.get("revoked"):
                continue
            out.append(rec)
    return out


def active_device_count(home: Path | str | None = None) -> int:
    return len(list_devices(home, include_revoked=False))


def device_public_meta(home: Path | str | None = None) -> list[dict[str, Any]]:
    """Owner-visible metadata: no public keys, no secrets.

    Revoked devices are hidden so a revoke visibly removes the phone from
    Connect settings. The record stays on disk so reconnects are refused.
    """
    rows = []
    for rec in list_devices(home, include_revoked=False):
        rows.append(
            {
                "id": rec.get("id"),
                "name": rec.get("name"),
                "paired_at": rec.get("paired_at"),
                "revoked": bool(rec.get("revoked")),
            }
        )
    return rows


def revoke_device(device_id: str, home: Path | str | None = None) -> dict[str, Any] | None:
    rec = get_device(device_id, home)
    if rec is None:
        return None
    rec["revoked"] = True
    save_device(rec, home)
    _revoked_live.add(str(rec["id"]))
    try:
        from remedy.connect.audit import append_event

        append_event("revoke", home=Path(home) if home else None, device_id=rec["id"])
    except Exception:
        pass
    return rec


def find_device_by_public(public_hex: str, home: Path | str | None = None) -> dict[str, Any] | None:
    want = str(public_hex or "").strip().lower()
    if not want:
        return None
    for rec in list_devices(home, include_revoked=True):
        if str(rec.get("public_hex") or "").strip().lower() == want:
            return rec
    return None


def load_state(home: Path | str | None = None) -> dict[str, Any]:
    path = state_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"paused": False}
    if not isinstance(raw, dict):
        return {"paused": False}
    return {"paused": bool(raw.get("paused", False))}


def save_state(state: dict[str, Any], home: Path | str | None = None) -> dict[str, Any]:
    clean = {"paused": bool(state.get("paused", False))}
    path = state_path(home)
    with _lock:
        write_json_atomic(path, clean, mode=0o600)
        _harden(path, is_dir=False)
    return clean


# ``is_paused`` runs before every inbound record on every phone socket; a
# short cache keeps that off the disk, and a torn read (Windows can raise
# PermissionError during an atomic replace) falls back to the last good value
# instead of momentarily reporting "not paused".
_PAUSED_TTL_S = 0.5
_paused_cache: dict[str, tuple[bool, float]] = {}


def is_paused(home: Path | str | None = None) -> bool:
    key = str(state_path(home))
    now = time.monotonic()
    hit = _paused_cache.get(key)
    if hit is not None and now - hit[1] < _PAUSED_TTL_S:
        return hit[0]
    try:
        val = bool(load_state(home).get("paused"))
    except Exception:
        val = hit[0] if hit is not None else False
    _paused_cache[key] = (val, now)
    return val


def set_paused(paused: bool, home: Path | str | None = None) -> None:
    was = is_paused(home)
    save_state({"paused": bool(paused)}, home)
    _paused_cache[str(state_path(home))] = (bool(paused), time.monotonic())
    if bool(paused) and not was:
        try:
            from remedy.connect.audit import append_event

            append_event("pause", home=Path(home) if home else None, on="1")
        except Exception:
            pass
    elif not paused and was:
        try:
            from remedy.connect.audit import append_event

            append_event("pause", home=Path(home) if home else None, on="0")
        except Exception:
            pass
