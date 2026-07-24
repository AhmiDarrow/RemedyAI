"""Local API authentication token for the desktop/sidecar agent.

Generates and persists a per-user token under ``~/.remedy/auth/local_api_token``
so the HTTP API is not open to every process by accident. The desktop shell
loads this token and sends ``Authorization: Bearer …`` on all mutating routes.

Disable with ``REMEDY_API_AUTH=0`` (tests / advanced local debugging).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILENAME = "local_api_token"
# Minimum length for accepted tokens
MIN_TOKEN_LEN = 16


def auth_enabled() -> bool:
    """Return False when API auth is explicitly disabled (tests)."""
    flag = str(os.environ.get("REMEDY_API_AUTH", "1")).strip().lower()
    if flag in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return True


def token_path(home: Path | str | None = None) -> Path:
    from remedy.interfaces.secret_store import auth_dir

    return auth_dir(home) / TOKEN_FILENAME


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
            existing = path.read_text(encoding="utf-8").strip()
            if len(existing) >= MIN_TOKEN_LEN:
                return existing
        except OSError as exc:
            logger.warning("Could not read local API token: %s", exc)

    tok = secrets.token_urlsafe(32)
    _persist(tok, home)
    logger.info("Generated local API token at %s", path)
    return tok


def _persist(token: str, home: Path | str | None) -> None:
    if not token or len(token) < MIN_TOKEN_LEN:
        return
    path = token_path(home)
    try:
        path.write_text(token.strip() + "\n", encoding="utf-8")
        from remedy.interfaces.secret_store import _harden_path

        _harden_path(path, is_dir=False)
    except OSError as exc:
        logger.warning("Could not persist local API token: %s", exc)


def load_local_api_token(home: Path | str | None = None) -> str:
    """Read token without generating (empty if missing)."""
    if not auth_enabled():
        return ""
    path = token_path(home)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""
