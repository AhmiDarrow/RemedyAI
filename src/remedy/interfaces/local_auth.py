"""Local API authentication token for the desktop/sidecar agent.

Generates and persists a per-user token under ``~/.remedy/auth/local_api_token``
so the HTTP API is not open to every process by accident. The desktop shell
loads this token and sends ``Authorization: Bearer …`` on all mutating routes.

On Windows the file is **DPAPI-sealed** (same envelope as xAI/Google auth) so a
casual disk copy or other local account cannot read the Bearer. Legacy plaintext
files are still accepted and upgraded on the next write.

Disable with ``REMEDY_API_AUTH=0`` (tests / advanced local debugging).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILENAME = "local_api_token"
# Minimum length for accepted tokens
MIN_TOKEN_LEN = 16


def auth_enabled() -> bool:
    """Return False when API auth is explicitly disabled (tests)."""
    flag = str(os.environ.get("REMEDY_API_AUTH", "1")).strip().lower()
    return flag not in ("0", "false", "no", "off", "disable", "disabled")


def _env_truthy(name: str) -> bool | None:
    """Return True/False if env is set to a known flag, else None (unset)."""
    if name not in os.environ:
        return None
    flag = str(os.environ.get(name, "")).strip().lower()
    if flag in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    if flag in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    return None


def _desktop_sidecar_context() -> bool:
    """True when running as packaged/desktop sidecar (IPC preferred over HTTP bootstrap)."""
    from remedy.core.runtime_identity import is_desktop_runtime

    return is_desktop_runtime()


def http_bootstrap_enabled() -> bool:
    """Whether loopback HTTP may hand out the local API token.

    **Power model:** desktop prefers OS/IPC (``get_local_api_token``) — that
    path never needs HTTP bootstrap. Browser Web UI needs bootstrap ON.

    Priority:
      1. ``REMEDY_HTTP_BOOTSTRAP`` env (explicit override, full owner control)
      2. ``http_bootstrap`` in config.toml (Settings toggle)
      3. Default **False** for packaged / desktop sidecar (safer IPC-only)
      4. Default **True** for plain ``remedy serve`` (Web UI / curl still work)

    Switch-to-WebUI can flip Settings ``http_bootstrap`` on, or set the env.
    """
    env = _env_truthy("REMEDY_HTTP_BOOTSTRAP")
    if env is not None:
        return env
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        if "http_bootstrap" in cfg:
            return bool(cfg.get("http_bootstrap"))
    except Exception:
        pass
    # Packaged desktop: safer default (IPC). Dev CLI serve: WebUI convenience.
    return not _desktop_sidecar_context()


def token_path(home: Path | str | None = None) -> Path:
    from remedy.interfaces.secret_store import auth_dir

    return auth_dir(home) / TOKEN_FILENAME


def token_encoding(home: Path | str | None = None) -> str:
    """How the local API token is stored: ``dpapi``, ``plain``, or ``missing``."""
    path = token_path(home)
    if not path.is_file():
        return "missing"
    try:
        raw = path.read_bytes()
    except OSError:
        return "missing"
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return "missing"
    if text.startswith("{"):
        try:
            outer = json.loads(text)
        except json.JSONDecodeError:
            return "plain"
        if isinstance(outer, dict) and outer.get("v") == 2 and outer.get("dpapi"):
            return "dpapi"
        if isinstance(outer, dict) and str(outer.get("encoding") or "") == "dpapi":
            return "dpapi"
    return "plain"


def _decode_token_bytes(raw: bytes) -> str:
    """Decode on-disk bytes → bearer token string (empty if unusable)."""
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    # DPAPI envelope (v2) — same shape as xAI/Google auth files.
    if text.startswith("{"):
        try:
            outer = json.loads(text)
        except json.JSONDecodeError:
            outer = None
        if isinstance(outer, dict) and outer.get("v") == 2 and outer.get("dpapi"):
            try:
                from remedy.interfaces.secret_store import _dpapi_unprotect

                plain = _dpapi_unprotect(base64.b64decode(str(outer["dpapi"])))
                return plain.decode("utf-8").strip()
            except Exception as exc:
                logger.warning("local API token DPAPI decrypt failed: %s", exc)
                return ""
        if isinstance(outer, dict) and str(outer.get("encoding") or "") == "dpapi":
            try:
                from remedy.interfaces.secret_store import _dpapi_unprotect

                blob = outer.get("payload") or outer.get("token") or ""
                plain = _dpapi_unprotect(base64.b64decode(str(blob)))
                return plain.decode("utf-8").strip()
            except Exception as exc:
                logger.warning("local API token DPAPI decrypt failed: %s", exc)
                return ""
        # Unknown JSON — not a valid bearer
        logger.warning("local API token file: unrecognized JSON envelope")
        return ""
    # Legacy plaintext token
    return text


def ensure_local_api_token(
    home: Path | str | None = None,
    *,
    explicit: str | None = None,
) -> str:
    """Return the API token to use for this process.

    Priority:
      1. ``explicit`` (caller / config)
      2. ``REMEDY_API_KEY`` env
      3. Existing on-disk token
      4. Generate + persist a new token

    When auth is disabled, returns empty string.
    """
    if not auth_enabled():
        return ""

    env_key = (os.environ.get("REMEDY_API_KEY") or "").strip()
    if explicit is not None and str(explicit).strip():
        tok = str(explicit).strip()
        _persist(tok, home)
        return tok
    if env_key:
        _persist(env_key, home)
        return env_key

    path = token_path(home)
    if path.is_file():
        try:
            existing = _decode_token_bytes(path.read_bytes())
            if len(existing) >= MIN_TOKEN_LEN:
                # Upgrade legacy plain → DPAPI when available (best-effort).
                if token_encoding(home) == "plain":
                    try:
                        from remedy.interfaces.secret_store import _dpapi_available

                        if _dpapi_available():
                            _persist(existing, home)
                    except Exception:
                        pass
                return existing
            # Windows DPAPI envelope we cannot unwrap (Linux/WSL sharing
            # ~/.remedy). Never overwrite the owner file — use a sidecar.
            if token_encoding(home) == "dpapi":
                alt = path.with_name("local_api_token.posix")
                if alt.is_file():
                    alt_tok = _decode_token_bytes(alt.read_bytes())
                    if len(alt_tok) >= MIN_TOKEN_LEN:
                        return alt_tok
                tok = secrets.token_urlsafe(32)
                _persist(tok, home, dest=alt)
                logger.info(
                    "Windows DPAPI token unreadable here; wrote %s", alt
                )
                return tok
        except OSError as exc:
            logger.warning("Could not read local API token: %s", exc)

    tok = secrets.token_urlsafe(32)
    _persist(tok, home)
    logger.info("Generated local API token at %s", path)
    return tok


def _persist(
    token: str,
    home: Path | str | None,
    *,
    dest: Path | None = None,
) -> None:
    if not token or len(token) < MIN_TOKEN_LEN:
        return
    path = dest if dest is not None else token_path(home)
    try:
        # Atomic write (unique scratch name, cleaned up on failure) so a crash
        # cannot leave an empty/half token file.
        from remedy.core.atomic_json import write_bytes_atomic

        data = _encode_token_bytes(token.strip())
        write_bytes_atomic(path, data, mode=0o600)
        from remedy.interfaces.secret_store import _harden_path

        _harden_path(path, is_dir=False)
    except OSError as exc:
        logger.warning("Could not persist local API token: %s", exc)


def _encode_token_bytes(token: str) -> bytes:
    """Encode bearer for disk: DPAPI envelope when available, else plaintext."""
    plain = token.strip().encode("utf-8")
    try:
        from remedy.interfaces.secret_store import _dpapi_available, _dpapi_protect

        if _dpapi_available():
            sealed = _dpapi_protect(plain)
            envelope = {
                "v": 2,
                "dpapi": base64.b64encode(sealed).decode("ascii"),
                "updated_at": time.time(),
            }
            return (json.dumps(envelope, indent=2) + "\n").encode("utf-8")
    except Exception as exc:
        logger.warning(
            "local API token DPAPI protect failed; storing ACL-only plain: %s",
            exc,
        )
    return plain + b"\n"


def load_local_api_token(home: Path | str | None = None) -> str:
    """Read token without generating (empty if missing)."""
    if not auth_enabled():
        return ""
    path = token_path(home)
    try:
        if path.is_file():
            return _decode_token_bytes(path.read_bytes())
    except OSError:
        return ""
    return ""
