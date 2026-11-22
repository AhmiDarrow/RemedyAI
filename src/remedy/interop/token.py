"""Inbound bearer token. Stored under {home}/interop/, never logged or committed."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from remedy.interop.paths import resolve_home, token_path

ENV_NAME = "REMEDY_INTEROP_TOKEN"


def resolve_token(home: str | Path | None = None, *, create: bool = False) -> str:
    """Return the inbound token: env wins, else the on-disk file.

    When create=True and neither is set, a token is generated and written
    with mode 0600. The value is never logged by this module.
    """
    env = (os.environ.get(ENV_NAME) or "").strip()
    if env:
        return env
    path = token_path(home)
    if path.is_file():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value
    if not create:
        return ""
    value = secrets.token_urlsafe(32)
    write_token(path, value)
    return value


def write_token(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def token_matches(provided: str | None, home: str | Path | None = None) -> bool:
    want = resolve_token(home, create=False)
    if not want or not provided:
        return False
    got = provided.strip()
    if got.lower().startswith("bearer "):
        got = got[7:].strip()
    if len(got) != len(want):
        # Still compare to keep the timing closer to constant-length.
        return False
    return secrets.compare_digest(got, want)
