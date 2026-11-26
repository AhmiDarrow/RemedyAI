"""Peers get plain English plus structured JSON. ril1 never leaves the mind."""

from __future__ import annotations

import json
import re
from typing import Any

_RIL_RE = re.compile(r"^\s*ril1\b", re.IGNORECASE)


def for_peer(text: str, *, home: str | None = None) -> tuple[str, dict[str, Any] | None]:
    """Return (plain_english, optional_structured) for an outbound peer message."""
    raw = (text or "").strip()
    if not raw:
        return "", None
    if not _RIL_RE.match(raw) and "ril1 " not in raw[:200]:
        structured = _maybe_json(raw)
        return raw, structured
    try:
        from remedy.core.inner_language.codec import decode, format_natural
        from remedy.core.inner_language.lexicon import load_lexicon

        lexicon = None
        if home:
            with _SuppressExc():
                lexicon = load_lexicon(home)
        rec = decode(raw, lexicon=lexicon)
        decoded = rec.get("decoded") or format_natural(str(rec.get("kind") or "note"), rec)
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else None
        if not payload and isinstance(rec, dict):
            payload = {k: v for k, v in rec.items() if k not in {"dense", "raw", "decoded"} and v is not None}
        return str(decoded or raw), payload or None
    except Exception:
        # Fail open to a scrubbed plain string rather than leaking dense terms.
        scrubbed = re.sub(r"ril1\s+\S+", "[inner note]", raw)
        return scrubbed, None


def inbound_plain(text: str) -> str:
    """Normalize inbound peer text. Peers should already send English; still scrub ril1."""
    english, _ = for_peer(text)
    return english


class _SuppressExc:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return True


def _maybe_json(text: str) -> dict[str, Any] | None:
    if not text.startswith("{") and not text.startswith("["):
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else {"value": value}
