"""Live call transcript — plain turns, then a note she can remember.

Nothing here is a recording of the audio. It is who said what, in words,
so the owner can review a call the way they review a storyline.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_text_atomic

logger = logging.getLogger(__name__)


def _dir(home: Path | str | None = None) -> Path:
    import os

    base = Path(home or os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    d = base / "telephony" / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Turn:
    who: str
    text: str
    at: float = field(default_factory=time.time)


@dataclass
class CallTranscript:
    call_id: str
    remote: str = ""
    turns: list[Turn] = field(default_factory=list)

    def add(self, who: str, text: str, at: float | None = None) -> None:
        body = (text or "").strip()
        if not body:
            return
        self.turns.append(Turn(who=who, text=body, at=at if at is not None else time.time()))

    def public(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "remote": self.remote,
            "turns": [{"who": t.who, "text": t.text, "at": t.at} for t in self.turns],
        }

    def as_plain(self) -> str:
        lines = [f"Call {self.call_id} with {self.remote or 'unknown'}"]
        for t in self.turns:
            label = "them" if t.who in ("far", "them", "remote") else "Remedy"
            lines.append(f"{label}: {t.text}")
        return "\n".join(lines)

    def save(self, home: Path | str | None = None) -> Path:
        dest = _dir(home) / f"{self.call_id}.json"
        write_text_atomic(dest, json.dumps(self.public(), indent=2) + "\n")
        return dest


def load(call_id: str, home: Path | str | None = None) -> CallTranscript | None:
    p = _dir(home) / f"{call_id}.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    tr = CallTranscript(call_id=str(raw.get("call_id") or call_id), remote=str(raw.get("remote") or ""))
    for row in raw.get("turns") or []:
        if isinstance(row, dict) and row.get("text"):
            tr.add(str(row.get("who") or "them"), str(row["text"]), float(row.get("at") or 0))
    return tr
