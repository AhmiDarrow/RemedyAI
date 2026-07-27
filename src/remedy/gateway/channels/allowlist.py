"""Unified allowlist checks for messenger inbound."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def parse_ids(raw: list[str] | str | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        parts = raw.replace(";", ",").split(",")
        return frozenset(p.strip() for p in parts if p.strip())
    return frozenset(str(x).strip() for x in raw if str(x).strip())


def env_allow_all(env_key: str) -> bool:
    return str(os.environ.get(env_key, "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_allowed(
    *,
    allowlist: frozenset[str],
    allow_all: bool,
    candidates: list[str],
    channel: str = "",
) -> bool:
    """True if allow_all or any candidate id is in allowlist.

    Empty allowlist + allow_all False → reject (secure default).
    """
    cleaned = [str(c).strip() for c in candidates if str(c).strip()]
    if allow_all:
        return True
    if not allowlist:
        logger.info("%s inbound dropped (empty allowlist)", channel or "messenger")
        return False
    for c in cleaned:
        if c in allowlist:
            return True
    logger.info(
        "%s inbound dropped (ids=%s not in allowlist)",
        channel or "messenger",
        cleaned,
    )
    return False
