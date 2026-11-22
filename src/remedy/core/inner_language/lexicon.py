"""Persisted, versioned lexicon with lineage, caps, and rollback."""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.core.inner_language.codec import CORE_TAGS
from remedy.core.inner_language.measure import mean_tokens

LEXICON_NAME = "inner_lexicon.json"
SCHEMA = 1
DEFAULT_CAP = 128
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,24}$")


def _home(home: str | Path | None) -> Path | None:
    if home:
        return Path(home).expanduser()
    with suppress(Exception):
        from remedy.interfaces.config import get_home_dir

        return Path(get_home_dir())
    return None


def lexicon_path(home: str | Path | None) -> Path | None:
    root = _home(home)
    return (root / LEXICON_NAME) if root else None


def _cap() -> int:
    try:
        from remedy.core.organism_knobs import resolve_int

        return resolve_int("inner_lexicon_cap")
    except Exception:
        return DEFAULT_CAP


def empty_lexicon() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": 1,
        "terms": {},
        "core": list(CORE_TAGS),
        "history": [],
        "updated_ts": 0.0,
    }


def load_lexicon(home: str | Path | None) -> dict[str, Any]:
    state = empty_lexicon()
    path = lexicon_path(home)
    if path is None or not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(raw, dict):
        return state
    state.update({k: raw[k] for k in state if k in raw})
    terms = state.get("terms") if isinstance(state.get("terms"), dict) else {}
    clean = {}
    for key, val in terms.items():
        if not isinstance(val, dict):
            continue
        if not str(val.get("gloss") or "").strip():
            continue
        clean[str(key)] = val
    state["terms"] = clean
    state["core"] = list(CORE_TAGS)
    return state


def save_lexicon(state: dict[str, Any], home: str | Path | None) -> None:
    path = lexicon_path(home)
    if path is None:
        return
    state = dict(state)
    state["schema"] = SCHEMA
    state["core"] = list(CORE_TAGS)
    state["updated_ts"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, state, indent=2, ensure_ascii=False)


def public_lexicon(state: dict[str, Any] | None = None, home: str | Path | None = None) -> dict[str, Any]:
    if state is None:
        state = load_lexicon(home)
    terms = []
    for key, val in (state.get("terms") or {}).items():
        terms.append(
            {
                "term": key,
                "gloss": val.get("gloss"),
                "coined_ts": val.get("coined_ts"),
                "source": val.get("source"),
                "from": val.get("from"),
                "uses": val.get("uses") or 0,
                "decode_ok": val.get("decode_ok") or 0,
                "decode_fail": val.get("decode_fail") or 0,
                "tokens_saved": val.get("tokens_saved") or 0,
                "core": False,
            }
        )
    terms.sort(key=lambda t: str(t.get("term") or ""))
    return {
        "version": state.get("version") or 1,
        "core": list(CORE_TAGS),
        "terms": terms,
        "count": len(terms),
        "cap": _cap(),
    }


def _term_from(phrase: str, existing: set[str]) -> str | None:
    words = re.findall(r"[A-Za-z][A-Za-z0-9']+", phrase.lower())
    stop = {
        "the", "and", "for", "that", "with", "this", "from", "about", "have",
        "been", "they", "them", "her", "his", "she", "him", "our", "are",
        "was", "were", "will", "just", "into", "over", "when", "what",
    }
    keep = [w for w in words if w not in stop and len(w) >= 3][:3]
    if not keep:
        return None
    stem = "".join(keep)[:20]
    if not _WORD.match(stem) or stem in CORE_TAGS or stem in existing:
        # suffix to avoid collision
        for i in range(2, 8):
            cand = f"{stem}{i}"[:24]
            if cand not in existing and cand not in CORE_TAGS and _WORD.match(cand):
                return cand
        return None
    return stem


def consider_coinage(
    home: str | Path | None,
    phrases: list[str],
    *,
    source: str = "dream",
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Coin compact terms for recurring phrases that save tokens and have a gloss."""
    try:
        from remedy.core.organism_knobs import resolve_bool, resolve_float

        if not resolve_bool("inner_language") or not resolve_bool("inner_coinage"):
            return []
        rate = float(resolve_float("inner_coinage_rate"))
    except Exception:
        rate = 0.3
    if rate <= 0:
        return []
    state = load_lexicon(home)
    terms = state.setdefault("terms", {})
    cap = _cap()
    coined: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for p in phrases:
        s = re.sub(r"\s+", " ", str(p or "").strip())
        if len(s) < 16:
            continue
        counts[s] = counts.get(s, 0) + 1
    # Already-coined phrases should not be re-coined.
    existing_from = {str(v.get("from") or "") for v in terms.values()}
    existing_keys = set(terms)
    # Sort by recurrence then length (longer phrases save more).
    candidates = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    budget = max(1, int(round(3 * max(0.0, min(1.0, rate)))))
    ts = now if now is not None else time.time()
    for phrase, n in candidates:
        if len(coined) >= budget or len(terms) >= cap:
            break
        if n < 2 or phrase in existing_from:
            continue
        term = _term_from(phrase, existing_keys)
        if not term:
            continue
        # Gloss is required and is the originating phrase, tightened.
        gloss = phrase[:160]
        saved = mean_tokens(phrase) - mean_tokens(term)
        # Amortized: term must beat the phrase after a few reuses, gloss paid once.
        if saved * max(3, n) <= mean_tokens(gloss) * 0.25 and saved <= 1.0:
            continue
        entry = {
            "term": term,
            "gloss": gloss,
            "coined_ts": ts,
            "source": source,
            "from": phrase,
            "uses": 0,
            "decode_ok": 0,
            "decode_fail": 0,
            "tokens_saved": 0,
            "core": False,
        }
        terms[term] = entry
        existing_keys.add(term)
        coined.append(entry)
    if coined:
        # Snapshot for rollback before bumping the version.
        hist = list(state.get("history") or [])
        hist.append({"version": state.get("version") or 1, "ts": ts, "added": [c["term"] for c in coined]})
        state["history"] = hist[-20:]
        state["version"] = int(state.get("version") or 1) + 1
        state["terms"] = terms
        save_lexicon(state, home)
    return coined


def note_comprehension(home: str | Path | None, *, used: list[str] | None = None, missed: list[str] | None = None) -> None:
    state = load_lexicon(home)
    terms = state.get("terms") or {}
    changed = False
    for name in used or []:
        if name in terms:
            terms[name]["uses"] = int(terms[name].get("uses") or 0) + 1
            terms[name]["decode_ok"] = int(terms[name].get("decode_ok") or 0) + 1
            changed = True
    for name in missed or []:
        if name in terms:
            terms[name]["uses"] = int(terms[name].get("uses") or 0) + 1
            terms[name]["decode_fail"] = int(terms[name].get("decode_fail") or 0) + 1
            changed = True
    if changed:
        state["terms"] = terms
        pruned = prune_and_maybe_rollback(state)
        save_lexicon(pruned, home)


def prune_and_maybe_rollback(state: dict[str, Any]) -> dict[str, Any]:
    terms = dict(state.get("terms") or {})
    cap = _cap()
    drop: list[str] = []
    for key, val in list(terms.items()):
        if val.get("core") or key in CORE_TAGS:
            continue
        uses = int(val.get("uses") or 0)
        fail = int(val.get("decode_fail") or 0)
        ok = int(val.get("decode_ok") or 0)
        # High failure rate after a few tries, or unused for a long while after coinage, dies.
        if uses >= 3 and fail > ok:
            drop.append(key)
        elif uses == 0 and fail >= 2:
            drop.append(key)
    for key in drop:
        terms.pop(key, None)
    # Cap: evict unused / low-ok first.
    if len(terms) > cap:
        ranked = sorted(
            terms.items(),
            key=lambda kv: (
                int(kv[1].get("decode_ok") or 0),
                int(kv[1].get("uses") or 0),
                float(kv[1].get("coined_ts") or 0),
            ),
        )
        for key, _ in ranked[: len(terms) - cap]:
            if key not in CORE_TAGS:
                terms.pop(key, None)
                drop.append(key)
    if drop:
        hist = list(state.get("history") or [])
        hist.append({"version": state.get("version") or 1, "ts": time.time(), "pruned": drop})
        state["history"] = hist[-20:]
        state["version"] = int(state.get("version") or 1) + 1
        # If we pruned because of comprehension regressions, the history
        # snapshot is the rollback record (previous version's added list).
    state["terms"] = terms
    return state


def lexicon_slice(home: str | Path | None, terms: list[str] | None = None, *, limit: int = 24) -> list[str]:
    """Compact `term:gloss` lines for a child-worker brief."""
    state = load_lexicon(home)
    wanted = [t for t in (terms or []) if t]
    items = state.get("terms") or {}
    if not wanted:
        # Most used, then newest.
        wanted = sorted(items, key=lambda k: (int(items[k].get("uses") or 0), float(items[k].get("coined_ts") or 0)), reverse=True)
    out: list[str] = []
    for key in wanted:
        val = items.get(key)
        if not val:
            continue
        gloss = str(val.get("gloss") or "").strip()
        if not gloss:
            continue
        out.append(f"{key}:{gloss}")
        if len(out) >= limit:
            break
    return out
