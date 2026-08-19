"""The agreement every owner makes once, before Remedy does anything for them.

Remedy is not a chat window: it runs commands, edits files, spends money through
accounts the owner connects, and — where enabled — speaks to other people. That
is the product, and it is also the risk, so the owner is told plainly and once,
in conversation, that **every action it takes is theirs** and that there is no
warranty and no liability beyond the LICENSE.

Design notes worth keeping:

* **Once, and short.** Five points, said aloud. Terms nobody listens to protect
  nobody, and a wall of text on first run teaches owners to click past exactly
  the warnings that matter later.
* **Versioned.** A material change re-asks and leads with *what changed* rather
  than replaying the whole thing.
* **Scoped.** Riskier capabilities layer their own agreement on top of this one
  (``telephony.consent``), so agreeing to use a file manager is not silently
  agreeing to let a machine phone strangers.

Full text: ``docs/TERMS.md``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from remedy.core.atomic_json import write_text_atomic

logger = logging.getLogger(__name__)

#: Bump only for a *material* change. Every bump re-asks every owner.
TERMS_VERSION = 1

TERMS_CHANGES: dict[int, str] = {
    1: "the first version of the terms",
}

DOC = "docs/TERMS.md"

SPOKEN_POINTS: tuple[str, ...] = (
    "I do things rather than just talk — run commands, change files, use your "
    "accounts. Everything I do counts as you doing it.",
    "I get things wrong sometimes, and I can be misled by something I read. "
    "Keep backups and check anything that matters.",
    "There is no warranty and no liability beyond the licence. If it is not "
    "covered there, it is not covered.",
    "Your model keys and any accounts you connect are yours — their terms, "
    "their bills, including anything I spend through them.",
    "Using me lawfully where you live is your call, and I will always stop for "
    "you before spending money.",
)


def _path(home: Path | str | None = None) -> Path:
    base = Path(home or os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base / "terms.json"


@dataclass(frozen=True, slots=True)
class Agreement:
    version: int = 0
    at: str = ""

    @property
    def current(self) -> bool:
        return self.version >= TERMS_VERSION

    @property
    def stale(self) -> bool:
        return 0 < self.version < TERMS_VERSION


#: Said aloud, so the number is a word. Derived rather than written into the
#: sentence: "five things" followed by six things is the kind of small wrongness
#: that makes everything else she says sound unreliable.
_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def _count(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def read(home: Path | str | None = None) -> Agreement:
    try:
        raw = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Agreement()
    if not isinstance(raw, dict):
        return Agreement()
    try:
        return Agreement(
            version=int(raw.get("version") or 0), at=str(raw.get("at") or "")
        )
    except (TypeError, ValueError):
        return Agreement()


def accept(home: Path | str | None = None) -> Agreement:
    """Record agreement. Only called after the points were actually said."""
    agreed = Agreement(version=TERMS_VERSION, at=datetime.now(UTC).isoformat())
    write_text_atomic(
        _path(home),
        json.dumps({"version": agreed.version, "at": agreed.at, "doc": DOC}, indent=2)
        + "\n",
    )
    logger.info("terms v%d accepted", agreed.version)
    return agreed


def withdraw(home: Path | str | None = None) -> None:
    with contextlib.suppress(OSError):
        _path(home).unlink()


def ask(home: Path | str | None = None) -> str:
    """What she says on first run. Empty once agreed."""
    agreed = read(home)
    if agreed.current:
        return ""
    lead = f"Before we start, {_count(len(SPOKEN_POINTS))} things you should hear once."
    if agreed.stale:
        changed = TERMS_CHANGES.get(TERMS_VERSION, "the terms have changed")
        lead = f"The terms have changed — {changed}. Worth hearing again."
    points = "\n".join(f"- {p}" for p in SPOKEN_POINTS)
    return f"{lead}\n{points}\nThe full version is in {DOC}. Happy to go ahead?"


class TermsNotAcceptedError(RuntimeError):
    """Raised instead of acting. Carries something sayable, not a code."""

    def __init__(self, home: Path | str | None = None) -> None:
        super().__init__(ask(home) or "The terms have not been agreed yet.")


def require(home: Path | str | None = None) -> None:
    """Gate anything that acts on the owner's behalf."""
    if not read(home).current:
        raise TermsNotAcceptedError(home)


def accepted(home: Path | str | None = None) -> bool:
    return read(home).current
