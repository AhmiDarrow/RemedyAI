"""Coin and prune during dreams and reflection; pack worker briefs."""

from __future__ import annotations

from typing import Any

from remedy.core.inner_language.audit import record_audit
from remedy.core.inner_language.codec import decode, encode, format_natural
from remedy.core.inner_language.lexicon import (
    consider_coinage,
    lexicon_slice,
    load_lexicon,
    note_comprehension,
)


def evolve_from_phrases(home: str | Any, phrases: list[str], *, source: str = "dream") -> list[dict[str, Any]]:
    coined = consider_coinage(home, phrases, source=source)
    for entry in coined:
        dense = encode(
            "coin",
            {"term": entry["term"], "gloss": entry["gloss"], "from": entry.get("from") or ""},
        )
        record_audit(home, kind="coin", dense=dense, decoded=format_natural("coin", entry))
    return coined


def pack_brief(
    home: str | Any,
    *,
    task: str,
    constraints: list[str] | None = None,
    brief_id: str = "",
    terms: list[str] | None = None,
) -> str:
    slice_ = lexicon_slice(home, terms)
    fields = {
        "id": brief_id or "brief",
        "tsk": task,
        "cst": constraints or [],
        "lex": slice_,
    }
    dense = encode("brief", fields)
    rec = decode(dense, lexicon=load_lexicon(home))
    record_audit(home, kind="brief", dense=dense, decoded=rec["decoded"])
    return dense


def unpack_report(home: str | Any, text: str) -> dict[str, Any]:
    rec = decode(text, lexicon=load_lexicon(home))
    fields = rec.get("fields") or {}
    used = fields.get("used") or []
    missed = fields.get("miss") or []
    if not isinstance(used, list):
        used = [used] if used else []
    if not isinstance(missed, list):
        missed = [missed] if missed else []
    note_comprehension(home, used=[str(u) for u in used], missed=[str(m) for m in missed])
    record_audit(home, kind="report", dense=text, decoded=rec.get("decoded") or text)
    rec["ok"] = fields.get("ok", True)
    rec["out"] = fields.get("out") or rec.get("decoded")
    return rec


def encode_state(home: str | Any, fields: dict[str, Any]) -> str:
    dense = encode("state", fields)
    rec = decode(dense, lexicon=load_lexicon(home))
    record_audit(home, kind="state", dense=dense, decoded=rec["decoded"])
    return dense
