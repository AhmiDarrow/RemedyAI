"""Secure at-rest storage for provider API keys.

Design goals
------------
* Never persist secrets in ``config.toml`` / YAML (those are easy to backup,
  commit, or share by accident).
* Store keys under ``~/.remedy/auth/provider_keys.json`` with restrictive
  filesystem permissions.
* On Windows, encrypt payloads with **DPAPI** (user-scoped CryptProtectData)
  so the file is opaque to other local accounts and casual disk copies.
* On non-Windows, store JSON with mode ``0o600`` (owner read/write only).
* Public APIs only ever expose booleans / fingerprints, never raw keys.

This module is intentionally dependency-free (stdlib + ctypes on Windows).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STORE_VERSION = 2
STORE_FILENAME = "provider_keys.json"

# Harden (icacls on Windows) is ~80–120ms per path. Only do it when creating
# the auth dir or writing the store — never on every secrets read.
_hardened_paths: set[str] = set()

# mtime-cached decrypted key map (process-local).
_keys_cache: dict[str, Any] = {"path": None, "mtime": None, "size": None, "data": None}


# ---------------------------------------------------------------------------
# Paths + filesystem hardening
# ---------------------------------------------------------------------------


def auth_dir(home: Path | str | None = None) -> Path:
    base = Path(home or os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    d = base / "auth"
    existed = d.is_dir()
    d.mkdir(parents=True, exist_ok=True)
    # Harden only on first create or once per process (not every GET /settings).
    key = str(d.resolve()) if d.exists() else str(d)
    if not existed or key not in _hardened_paths:
        _harden_path(d, is_dir=True)
        _hardened_paths.add(key)
    return d


def store_path(home: Path | str | None = None) -> Path:
    return auth_dir(home) / STORE_FILENAME


def _harden_path(path: Path, *, is_dir: bool = False) -> None:
    """Best-effort restricted access (POSIX mode + Windows ACLs).

    On Windows we deliberately grant **user + Administrators + SYSTEM**.
    Granting *only* the username after ``/inheritance:r`` can produce an empty
    or unusable DACL under UAC-filtered admin tokens (Medium IL), locking the
    process out of its own secrets. We never leave a path unreadable by the
    current process — if a post-check fails, we restore broad user access.
    """
    try:
        if is_dir:
            path.chmod(0o700)
        else:
            path.chmod(0o600)
    except OSError:
        pass

    if sys.platform != "win32":
        return
    try:
        user = os.environ.get("USERNAME") or os.getlogin()
    except OSError:
        user = os.environ.get("USERNAME") or ""
    if not user or not path.exists():
        return
    try:
        import subprocess

        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        # Directory vs file ACE syntax
        user_ace = f"{user}:(OI)(CI)F" if is_dir else f"{user}:F"
        adm_ace = "Administrators:(OI)(CI)F" if is_dir else "Administrators:F"
        sys_ace = "SYSTEM:(OI)(CI)F" if is_dir else "SYSTEM:F"

        def _run(args: list[str]) -> None:
            subprocess.run(
                args,
                check=False,
                capture_output=True,
                creationflags=flags,
                timeout=8,
            )

        # Remove inheritance, then grant a safe set of principals.
        _run(["icacls", str(path), "/inheritance:r"])
        _run(["icacls", str(path), "/grant:r", user_ace])
        _run(["icacls", str(path), "/grant:r", adm_ace])
        _run(["icacls", str(path), "/grant:r", sys_ace])
        # Drop accidental Everyone if present
        _run(["icacls", str(path), "/remove:g", "Everyone"])

        # Post-check: current process must still be able to read.
        try:
            if is_dir:
                next(path.iterdir(), None)
            else:
                path.read_bytes()[:1]
        except OSError:
            # Fail closed — NEVER grant Everyone:F (that is worse than default ACLs).
            logger.error(
                "ACL harden left %s unreadable by this process; restoring "
                "user+Administrators+SYSTEM only (no Everyone). Check UAC / IL.",
                path,
            )
            _run(["icacls", str(path), "/inheritance:r"])
            _run(["icacls", str(path), "/grant:r", user_ace])
            _run(["icacls", str(path), "/grant:r", adm_ace])
            _run(["icacls", str(path), "/grant:r", sys_ace])
            _run(["icacls", str(path), "/remove:g", "Everyone"])
            try:
                if is_dir:
                    next(path.iterdir(), None)
                else:
                    path.read_bytes()[:1]
            except OSError:
                logger.error(
                    "Still unreadable after ACL restore: %s — secrets may be "
                    "inaccessible until permissions are fixed manually.",
                    path,
                )
    except Exception as exc:
        logger.debug("icacls harden failed for %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Windows DPAPI (user-scoped)
# ---------------------------------------------------------------------------


def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_protect(plaintext: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_buf = ctypes.create_string_buffer(plaintext)
    in_blob = DATA_BLOB(len(plaintext), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()

    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "Remedy provider keys",
        None,
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    ):
        raise OSError(f"CryptProtectData failed (err={kernel32.GetLastError()})")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(ciphertext: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_buf = ctypes.create_string_buffer(ciphertext)
    in_blob = DATA_BLOB(len(ciphertext), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    ):
        raise OSError(f"CryptUnprotectData failed (err={kernel32.GetLastError()})")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _empty_payload() -> dict[str, str]:
    return {}


def _decode_store_file(raw: bytes) -> dict[str, str]:
    """Decode on-disk bytes → {provider: key}."""
    if not raw:
        return _empty_payload()

    # DPAPI whole-file envelope
    try:
        outer = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        outer = None

    if isinstance(outer, dict) and outer.get("encoding") == "dpapi":
        blob_b64 = outer.get("payload") or ""
        cipher = base64.b64decode(blob_b64)
        plain = _dpapi_unprotect(cipher)
        inner = json.loads(plain.decode("utf-8"))
        return _normalize_keys(inner.get("keys") if isinstance(inner, dict) else inner)

    if isinstance(outer, dict) and outer.get("encoding") in ("plain", None, ""):
        # Legacy / non-Windows plain JSON
        if "keys" in outer:
            return _normalize_keys(outer.get("keys"))
        # Flat map of provider → key (older draft)
        skip = {"version", "encoding", "updated_at", "payload", "encryption"}
        return _normalize_keys({k: v for k, v in outer.items() if k not in skip})

    # Unknown format
    logger.warning("provider secret store: unrecognized format; ignoring")
    return _empty_payload()


def _normalize_keys(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        pk = str(k or "").strip().lower()
        val = str(v or "").strip()
        if pk and val and not pk.startswith("_"):
            out[pk] = val
    return out


def _encode_store_file(keys: dict[str, str]) -> bytes:
    inner = {
        "version": STORE_VERSION,
        "keys": keys,
        "updated_at": time.time(),
    }
    plain = json.dumps(inner, indent=None, separators=(",", ":")).encode("utf-8")

    if _dpapi_available():
        try:
            cipher = _dpapi_protect(plain)
            outer = {
                "version": STORE_VERSION,
                "encoding": "dpapi",
                "payload": base64.b64encode(cipher).decode("ascii"),
                "updated_at": time.time(),
            }
            return (json.dumps(outer, indent=2) + "\n").encode("utf-8")
        except Exception as exc:
            logger.warning(
                "DPAPI protect failed (%s); falling back to owner-only plain store",
                exc,
            )

    outer = {
        "version": STORE_VERSION,
        "encoding": "plain",
        "keys": keys,
        "updated_at": time.time(),
    }
    return (json.dumps(outer, indent=2) + "\n").encode("utf-8")


def invalidate_provider_keys_cache() -> None:
    """Drop cached secrets (call after writes or tests)."""
    _keys_cache["path"] = None
    _keys_cache["mtime"] = None
    _keys_cache["size"] = None
    _keys_cache["data"] = None


def load_provider_keys(home: Path | str | None = None) -> dict[str, str]:
    """Return {provider: api_key} from the secure store (empty if missing).

    Cached by path mtime/size so Settings / chat key resolution stays cheap.
    Returns a shallow copy so callers cannot mutate the cache entry.
    """
    path = store_path(home)
    if not path.exists():
        return {}
    try:
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
        if (
            _keys_cache["path"] == str(path)
            and _keys_cache["mtime"] == mtime
            and _keys_cache["size"] == size
            and isinstance(_keys_cache["data"], dict)
        ):
            return dict(_keys_cache["data"])
        raw = path.read_bytes()
        data = _decode_store_file(raw)
        _keys_cache.update(
            {"path": str(path), "mtime": mtime, "size": size, "data": data}
        )
        return dict(data)
    except Exception as exc:
        logger.warning("Failed to load provider secret store: %s", exc)
        return {}


def save_provider_keys(keys: dict[str, str], home: Path | str | None = None) -> Path:
    """Overwrite the secure store with the given map. Returns path written."""
    path = store_path(home)
    cleaned = _normalize_keys(keys)
    data = _encode_store_file(cleaned)
    # Atomic-ish write
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    _harden_path(tmp, is_dir=False)
    tmp.replace(path)
    _harden_path(path, is_dir=False)
    parent_key = str(path.parent.resolve()) if path.parent.exists() else str(path.parent)
    _harden_path(path.parent, is_dir=True)
    _hardened_paths.add(parent_key)
    _hardened_paths.add(str(path.resolve()) if path.exists() else str(path))
    invalidate_provider_keys_cache()
    # Seed cache with what we just wrote (avoids immediate re-decrypt).
    try:
        st = path.stat()
        _keys_cache.update(
            {
                "path": str(path),
                "mtime": st.st_mtime,
                "size": st.st_size,
                "data": dict(cleaned),
            }
        )
    except OSError:
        pass
    return path


def set_provider_secret(
    provider: str,
    api_key: str | None,
    home: Path | str | None = None,
) -> dict[str, str]:
    """Set or clear one provider key. Returns the full key map (sensitive)."""
    provider = str(provider or "").strip().lower()
    if not provider:
        raise ValueError("provider is required")
    keys = load_provider_keys(home)
    val = (api_key or "").strip()
    if val:
        keys[provider] = val
    else:
        keys.pop(provider, None)
    save_provider_keys(keys, home=home)
    return keys


def get_provider_secret(
    provider: str,
    home: Path | str | None = None,
) -> str | None:
    provider = str(provider or "").strip().lower()
    if not provider:
        return None
    return load_provider_keys(home).get(provider) or None


def clear_provider_secret(
    provider: str | None = None,
    home: Path | str | None = None,
) -> None:
    """Clear one provider or all provider secrets."""
    if provider is None or str(provider).strip() == "":
        path = store_path(home)
        if path.exists():
            path.unlink()
        return
    set_provider_secret(str(provider).strip().lower(), None, home=home)


def providers_with_secrets(home: Path | str | None = None) -> dict[str, bool]:
    """Public map of which providers have a stored key (no secret values)."""
    return dict.fromkeys(load_provider_keys(home), True)


def fingerprint_key(api_key: str | None) -> str | None:
    """Short non-reversible fingerprint for UI/debug (never the key itself)."""
    k = (api_key or "").strip()
    if not k:
        return None
    digest = hashlib.sha256(k.encode("utf-8")).hexdigest()
    return digest[:12]


def public_secret_status(
    home: Path | str | None = None,
    *,
    include_fingerprints: bool = False,
) -> dict[str, Any]:
    """Safe status blob for GET /api/settings (no raw secrets).

    Fingerprints are opt-in — desktop UI only needs booleans, and hashing every
    key on each Settings open is pure overhead.
    """
    keys = load_provider_keys(home)
    path = store_path(home)
    encoding = "missing"
    if path.exists():
        try:
            # Outer envelope is plaintext JSON even when payload is DPAPI.
            # Avoid a second full decrypt path.
            outer = json.loads(path.read_bytes().decode("utf-8"))
            encoding = str(outer.get("encoding") or "unknown")
        except Exception:
            encoding = "unknown"
    out: dict[str, Any] = {
        "providers_with_keys": sorted(keys.keys()),
        "provider_keys_set": dict.fromkeys(keys, True),
        "store_path": str(path),
        "encoding": encoding,
    }
    if include_fingerprints:
        out["fingerprints"] = {k: fingerprint_key(v) for k, v in keys.items()}
    return out


# ---------------------------------------------------------------------------
# Migration from insecure config.toml
# ---------------------------------------------------------------------------


def migrate_secrets_from_config(
    cfg: dict[str, Any],
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Pull plaintext keys out of config into the secret store; scrub config.

    Handles:
      * ``provider_keys`` table
      * legacy ``llm_api_key`` (assigned to active / inferred provider)

    Returns a **scrubbed** config dict safe to write back to disk.
    Does not write the config file itself — caller persists.
    """
    from remedy.interfaces.config import (
        infer_key_provider,
        looks_like_xai_credential,
    )

    cfg = dict(cfg or {})
    home_path = Path(home).expanduser() if home else None
    if home_path is None and cfg.get("home_dir"):
        home_path = Path(str(cfg["home_dir"])).expanduser()

    stored = load_provider_keys(home_path)
    changed = False

    # Table of per-provider keys in config
    raw_table = cfg.get("provider_keys")
    if isinstance(raw_table, dict):
        for k, v in raw_table.items():
            pk = str(k or "").strip().lower()
            val = str(v or "").strip()
            if pk and val and pk not in stored:
                stored[pk] = val
                changed = True
            elif pk and val and stored.get(pk) != val:
                # Config still has a key — prefer non-empty config once, then scrub
                stored[pk] = val
                changed = True

    # Legacy single global key
    global_key = str(cfg.get("llm_api_key") or "").strip()
    provider = str(cfg.get("llm_provider") or "").strip().lower()
    last = str(cfg.get("last_llm_provider") or "").strip().lower()
    if global_key:
        if provider == "xai" and not looks_like_xai_credential(global_key):
            owner = last if last and last != "xai" else (infer_key_provider(global_key) or "deepseek")
        elif provider:
            if provider == "xai" and not looks_like_xai_credential(global_key):
                owner = infer_key_provider(global_key) or "deepseek"
            else:
                owner = provider
        else:
            owner = infer_key_provider(global_key) or "custom"
        if owner and global_key:
            # Don't park non-xAI material under xai
            if owner == "xai" and not looks_like_xai_credential(global_key):
                owner = infer_key_provider(global_key) or "deepseek"
            if owner not in stored:
                stored[owner] = global_key
                changed = True

    if changed or stored:
        save_provider_keys(stored, home=home_path)

    # Scrub secrets from config representation
    cfg.pop("provider_keys", None)
    if cfg.get("llm_api_key"):
        cfg["llm_api_key"] = ""
    # Marker so UI knows secrets live in the store (not a secret)
    cfg["secrets_store"] = "auth/provider_keys.json"
    return cfg


def scrub_config_secrets(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of cfg with secret fields removed (safe to write to disk)."""
    out = dict(cfg or {})
    out.pop("provider_keys", None)
    if "llm_api_key" in out:
        out["llm_api_key"] = ""
    return out
