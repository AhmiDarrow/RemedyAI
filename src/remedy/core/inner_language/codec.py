"""Versioned envelope: lossless structured encode/decode, natural fallback."""

from __future__ import annotations

import re
from typing import Any

from remedy.core.inner_language.capability import current_capability

VERSION = "ril1"
KINDS = ("state", "dream", "note", "brief", "report", "coin", "lex")
# Seed core tags. These never enter the coined lexicon and cannot be pruned.
CORE_TAGS = (
    "ril1",
    "state",
    "dream",
    "note",
    "brief",
    "report",
    "coin",
    "lex",
    "drv",
    "aff",
    "bio",
    "bel",
    "tsk",
    "cst",
    "out",
    "ok",
    "id",
    "gloss",
    "from",
    "used",
    "miss",
    "term",
    "ver",
)

# Fixed field order per kind (structured first, text last).
FIELDS: dict[str, tuple[str, ...]] = {
    "state": ("drv", "aff", "bio", "bel"),
    "dream": ("bel", "note"),
    "note": ("id", "note"),
    "brief": ("id", "tsk", "cst", "lex"),
    "report": ("id", "ok", "out", "used", "miss"),
    "coin": ("term", "gloss", "from"),
    "lex": ("ver", "lex"),
}

_VERBOSITY = ("off", "natural", "dense")


def _knob_verbosity() -> str:
    try:
        from remedy.core.organism_knobs import resolve, resolve_bool

        if not resolve_bool("inner_language"):
            return "off"
        raw = str(resolve("inner_language_verbosity") or "dense").strip().lower()
        return raw if raw in _VERBOSITY else "dense"
    except Exception:
        return "dense"


def _dump_drv(val: Any) -> str:
    if isinstance(val, dict):
        order = ("curiosity", "social", "competence", "rest")
        nums = [int(round(float(val.get(k, 0)) * 100)) for k in order]
    else:
        nums = [int(round(float(x))) for x in list(val)]
    nums = [max(0, min(100, n)) for n in nums[:4]]
    while len(nums) < 4:
        nums.append(0)
    return ",".join(str(n) for n in nums)


def _load_drv(raw: str) -> dict[str, float]:
    parts = [p for p in raw.split(",") if p.strip()]
    keys = ("curiosity", "social", "competence", "rest")
    out: dict[str, float] = {}
    for i, k in enumerate(keys):
        n = int(parts[i]) if i < len(parts) else 0
        out[k] = max(0.0, min(1.0, n / 100.0))
    return out


def _dump_text(val: Any) -> str:
    s = str(val or "")
    return f"{len(s)}:{s}"


def _load_text(raw: str) -> str:
    if ":" not in raw:
        return raw
    n_s, rest = raw.split(":", 1)
    try:
        n = int(n_s)
    except ValueError:
        return raw
    return rest[:n]


def _dump_list(val: Any) -> str:
    items = [str(x).strip() for x in (val or []) if str(x).strip()]
    blob = ",".join(items)
    return f"{len(blob)}:{blob}"


def _load_list(raw: str) -> list[str]:
    text = _load_text(raw)
    return [p for p in text.split(",") if p]


def dump_field(tag: str, val: Any) -> str:
    if tag == "drv":
        return _dump_drv(val)
    if tag in ("ok", "ver"):
        return str(int(val)) if not isinstance(val, str) else val
    if tag in ("bel", "used", "miss", "lex", "cst"):
        if isinstance(val, (list, tuple)):
            return _dump_list(val)
        return _dump_text(val)
    if tag in ("id", "term", "aff"):
        # Short atoms — length prefix costs more than it saves.
        return str(val or "").replace(" ", "_") if tag != "aff" else str(val or "")
    if tag in ("bio", "note", "tsk", "out", "gloss", "from"):
        return _dump_text(val)
    return _dump_text(val)


def load_field(tag: str, raw: str) -> Any:
    if tag == "drv":
        return _load_drv(raw)
    if tag in ("ok",):
        return bool(int(raw)) if raw in ("0", "1") else bool(raw)
    if tag in ("ver",):
        try:
            return int(raw)
        except ValueError:
            return raw
    if tag in ("bel", "used", "miss", "lex", "cst"):
        return _load_list(raw)
    return _load_text(raw)


def _split_fields(payload: str) -> dict[str, str]:
    """Split tagged fields. Length-prefixed values are consumed exactly."""
    parts: dict[str, str] = {}
    tags = sorted(CORE_TAGS, key=len, reverse=True)
    i, n = 0, len(payload)
    while i < n:
        while i < n and payload[i] == " ":
            i += 1
        found = None
        for tag in tags:
            if payload.startswith(tag + "=", i):
                found = tag
                break
        if not found:
            break
        i += len(found) + 1
        raw_start = i
        m = re.match(r"(\d+):", payload[i:])
        if m:
            count = int(m.group(1))
            i = i + m.end() + count
            parts[found] = payload[raw_start:i]
        else:
            j = n
            for tag in tags:
                k = i
                while True:
                    pos = payload.find(" " + tag + "=", k)
                    if pos == -1:
                        break
                    j = min(j, pos)
                    break
            parts[found] = payload[raw_start:j]
            i = j
    return parts



def encode(
    kind: str,
    fields: dict[str, Any],
    *,
    verbosity: str | None = None,
    local: bool = False,
) -> str:
    kind = (kind or "note").strip().lower()
    if kind not in KINDS:
        kind = "note"
    verb = (verbosity or _knob_verbosity()).strip().lower()
    if verb not in _VERBOSITY:
        verb = "dense"
    cap = current_capability(local=local)
    header = f"{VERSION} {kind}"
    if cap.level != 1:
        header += f" L={cap.level}"
    if verb != "dense":
        header += f" v={verb}"
    if verb == "off":
        return format_natural(kind, fields)
    if verb == "natural":
        return f"{header} {format_natural(kind, fields)}"
    chunks: list[str] = []
    for tag in FIELDS[kind]:
        if tag not in fields or fields[tag] is None:
            continue
        chunks.append(f"{tag}={dump_field(tag, fields[tag])}")
    if not chunks:
        return header
    return header + " " + " ".join(chunks)


def decode(text: str, *, lexicon: Any | None = None) -> dict[str, Any]:
    raw = (text or "").strip()
    out: dict[str, Any] = {
        "version": VERSION,
        "kind": "natural",
        "level": 1,
        "verbosity": "natural",
        "fields": {},
        "decoded": raw,
        "dense": raw,
    }
    if not raw:
        return out
    if not raw.startswith(VERSION + " ") and not raw.startswith(VERSION):
        out["decoded"] = expand_terms(raw, lexicon)
        return out
    parts = raw.split(" ", 3)
    # ril1 kind L=1 v=dense payload...
    kind = parts[1] if len(parts) > 1 else "note"
    if kind not in KINDS:
        kind = "note"
    out["kind"] = kind
    rest = raw[len(VERSION) + 1 + len(kind) :].lstrip()
    level, verb = 1, "dense"
    while rest.startswith("L=") or rest.startswith("v="):
        token, _, rest = rest.partition(" ")
        if token.startswith("L="):
            with _suppress():
                level = int(token[2:])
        elif token.startswith("v="):
            verb = token[2:] or "dense"
        rest = rest.lstrip()
    out["level"] = level
    out["verbosity"] = verb
    out["dense"] = raw
    if verb == "natural" or not rest:
        # payload is already natural after the header
        out["decoded"] = expand_terms(rest or format_natural(kind, {}), lexicon)
        if rest:
            out["fields"]["_natural"] = rest
        return out
    parsed = _split_fields(rest)
    fields: dict[str, Any] = {}
    for tag, blob in parsed.items():
        if tag in CORE_TAGS:
            try:
                fields[tag] = load_field(tag, blob)
            except Exception:
                fields[tag] = blob
    out["fields"] = fields
    out["decoded"] = expand_terms(format_natural(kind, fields), lexicon)
    return out


def format_natural(kind: str, fields: dict[str, Any]) -> str:
    if kind == "state":
        drv = fields.get("drv") or {}
        if isinstance(drv, dict):
            d = ", ".join(f"{k} {drv.get(k, 0):.2f}" for k in ("curiosity", "social", "competence", "rest"))
        else:
            d = str(drv)
        bits = [f"Drives: {d}."]
        if fields.get("aff"):
            bits.append(f"Feeling: {fields['aff']}.")
        if fields.get("bio"):
            bits.append(str(fields["bio"]))
        if fields.get("bel"):
            bits.append("Beliefs: " + "; ".join(fields["bel"][:6]))
        return " ".join(bits).strip()
    if kind == "brief":
        parts = []
        if fields.get("tsk"):
            parts.append(f"Goal: {fields['tsk']}")
        if fields.get("cst"):
            cst = fields["cst"]
            parts.append("Constraints: " + (", ".join(cst) if isinstance(cst, list) else str(cst)))
        if fields.get("lex"):
            lex = fields["lex"]
            if isinstance(lex, list):
                parts.append("Lexicon: " + ", ".join(lex[:24]))
        return " ".join(parts).strip() or "Brief."
    if kind == "report":
        ok = fields.get("ok")
        status = "ok" if ok else "failed"
        body = str(fields.get("out") or "").strip()
        return f"Report ({status}): {body}".strip()
    if kind == "coin":
        return f"Coined {fields.get('term')}: {fields.get('gloss')}"
    if kind == "dream":
        bits = []
        if fields.get("bel"):
            bits.append("Beliefs: " + "; ".join(fields["bel"][:8]))
        if fields.get("note"):
            bits.append(str(fields["note"]))
        return " ".join(bits).strip() or "Dream."
    if kind == "note":
        return str(fields.get("note") or "").strip() or "Note."
    if kind == "lex":
        lex = fields.get("lex") or []
        return "Lexicon slice: " + ", ".join(lex if isinstance(lex, list) else [str(lex)])
    return str(fields.get("note") or fields.get("bio") or "").strip()


def expand_terms(text: str, lexicon: Any) -> str:
    if not text:
        return ""
    if lexicon is None:
        return text
    terms = getattr(lexicon, "terms", None)
    if isinstance(lexicon, dict):
        terms = lexicon.get("terms") or lexicon
    if not isinstance(terms, dict):
        return text
    # Replace whole-word coined terms with their gloss, longest first.
    items = sorted(
        ((str(k), str((v.get("gloss") if isinstance(v, dict) else v) or "")) for k, v in terms.items()),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
    out = text
    for term, gloss in items:
        if not term or not gloss or term in CORE_TAGS:
            continue
        out = re.sub(rf"\b{re.escape(term)}\b", gloss, out)
    return out


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return True
