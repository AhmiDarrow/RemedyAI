"""Compact media digest the local mind writes for frontier / RMB muscle.

Pixels stay on this machine unless the mind sets needs_pixels (or the
owner turns on the Advanced knob always_send_images). The schema is
locked for the one training run — see /workspace/remedy-mind-ENGINE-NOTES.md
and docs/INFERENCE_ENGINE.md.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import Any

DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "needs_pixels"],
    "properties": {
        "summary": {"type": "string"},
        "text_in_image": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "layout": {"type": "string"},
        "ui_elements": {"type": "array", "items": {"type": "string"}},
        "metadata": {"type": "object"},
        "needs_pixels": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}

DIGEST_PROMPT = (
    "Describe this image for another model that cannot see the pixels. "
    "Reply with a single JSON object only, using these keys: "
    "summary (one or two sentences), text_in_image (all readable text or "
    "empty string), objects (array of labels), layout (short composition "
    "note), ui_elements (buttons, fields, menus), metadata (object), "
    "needs_pixels (true only if a frontier model truly must see the image), "
    "reason (why pixels are or are not required). Do not include a data URL."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def empty_digest(*, reason: str = "") -> dict[str, Any]:
    return {
        "summary": "",
        "text_in_image": "",
        "objects": [],
        "layout": "",
        "ui_elements": [],
        "metadata": {},
        "needs_pixels": False,
        "reason": reason,
    }


def parse_digest(text: str) -> dict[str, Any]:
    """Parse a model reply into the locked digest schema."""
    out = empty_digest()
    raw = (text or "").strip()
    if not raw:
        return out
    blob = raw
    if "```" in blob:
        parts = blob.split("```")
        for part in parts:
            piece = part.strip()
            if piece.startswith("json"):
                piece = piece[4:].strip()
            if piece.startswith("{"):
                blob = piece
                break
    match = _JSON_RE.search(blob)
    if match:
        blob = match.group(0)
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        out["summary"] = raw[:800]
        out["reason"] = "unparsed_model_text"
        return out
    if not isinstance(data, dict):
        out["summary"] = raw[:800]
        return out
    out["summary"] = str(data.get("summary") or "")
    out["text_in_image"] = str(data.get("text_in_image") or "")
    objs = data.get("objects") or []
    out["objects"] = [str(x) for x in objs] if isinstance(objs, list) else []
    out["layout"] = str(data.get("layout") or "")
    ui = data.get("ui_elements") or []
    out["ui_elements"] = [str(x) for x in ui] if isinstance(ui, list) else []
    meta = data.get("metadata")
    out["metadata"] = meta if isinstance(meta, dict) else {}
    out["needs_pixels"] = bool(data.get("needs_pixels"))
    out["reason"] = str(data.get("reason") or "")
    return out


def format_digest_for_muscle(digest: dict[str, Any], *, path: str = "") -> str:
    """Plain-text block handed to a frontier or RMB muscle model."""
    d = digest if isinstance(digest, dict) else empty_digest()
    lines = ["[Local mind image digest — pixels not attached]"]
    if path:
        lines.append(f"source: {path}")
    if d.get("summary"):
        lines.append(f"summary: {d['summary']}")
    if d.get("text_in_image"):
        lines.append(f"text_in_image: {d['text_in_image']}")
    if d.get("objects"):
        lines.append("objects: " + ", ".join(str(x) for x in d["objects"]))
    if d.get("layout"):
        lines.append(f"layout: {d['layout']}")
    if d.get("ui_elements"):
        lines.append("ui_elements: " + ", ".join(str(x) for x in d["ui_elements"]))
    if d.get("metadata"):
        with suppress(TypeError, ValueError):
            lines.append("metadata: " + json.dumps(d["metadata"], ensure_ascii=False))
    lines.append(f"needs_pixels: {bool(d.get('needs_pixels'))}")
    if d.get("reason"):
        lines.append(f"reason: {d['reason']}")
    return "\n".join(lines)


def always_send_images(cfg: dict[str, Any] | None = None) -> bool:
    try:
        from remedy.core.organism_knobs import resolve_bool

        return bool(resolve_bool("always_send_images", cfg=cfg))
    except Exception:
        return False
