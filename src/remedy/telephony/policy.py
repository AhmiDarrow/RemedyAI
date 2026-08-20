"""Per-contact call policy — disclosure, recording, whose voice.

Defaults protect the owner: she discloses that she is an assistant, she
notices recording, and she never wears the owner's voice unless a live
clone grant names this task. A contact row can override disclosure and
recording; it cannot override "never claim to be human when asked".
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_text_atomic

logger = logging.getLogger(__name__)


def _path(home: Path | str | None = None) -> Path:
    import os

    base = Path(home or os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    d = base / "telephony"
    d.mkdir(parents=True, exist_ok=True)
    return d / "policy.json"


@dataclass
class ContactPolicy:
    contact: str = "*"
    #: Say she is an assistant at the start of the call.
    disclose: bool = True
    #: Mention that a transcript may be kept.
    record_notice: bool = True
    #: Never a default. Only True when a live clone grant names this contact.
    use_owner_voice: bool = False
    notes: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)

    def opening_line(self) -> str:
        bits: list[str] = []
        if self.disclose:
            bits.append("I am Remedy, an assistant calling on behalf of my owner.")
        if self.record_notice:
            bits.append("I may keep a written note of what we say.")
        return " ".join(bits)


def _load_raw(home: Path | str | None = None) -> dict[str, Any]:
    try:
        raw = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def for_contact(contact: str, home: Path | str | None = None) -> ContactPolicy:
    raw = _load_raw(home)
    key = (contact or "*").strip() or "*"
    row = raw.get(key) if isinstance(raw.get(key), dict) else raw.get("*")
    if not isinstance(row, dict):
        row = {}
    return ContactPolicy(
        contact=key,
        disclose=bool(row.get("disclose", True)),
        record_notice=bool(row.get("record_notice", True)),
        use_owner_voice=bool(row.get("use_owner_voice", False)),
        notes=str(row.get("notes") or ""),
    )


def set_contact(
    contact: str,
    home: Path | str | None = None,
    *,
    disclose: bool | None = None,
    record_notice: bool | None = None,
    use_owner_voice: bool | None = None,
    notes: str | None = None,
) -> ContactPolicy:
    key = (contact or "*").strip() or "*"
    cur = for_contact(key, home)
    if disclose is not None:
        cur.disclose = bool(disclose)
    if record_notice is not None:
        cur.record_notice = bool(record_notice)
    if use_owner_voice is not None:
        cur.use_owner_voice = bool(use_owner_voice)
    if notes is not None:
        cur.notes = str(notes)
    cur.contact = key
    raw = _load_raw(home)
    raw[key] = cur.public()
    write_text_atomic(_path(home), json.dumps(raw, indent=2) + "\n")
    return cur
