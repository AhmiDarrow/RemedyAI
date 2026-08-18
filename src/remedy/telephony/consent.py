"""Agreeing to the phone terms, and fetching nothing until asked.

Two rules, both enforced here rather than trusted to callers.

**Nothing telephony-related ships with Remedy.** No SIP engine, no speech
models, no Android images. Bundling is redistribution, and redistribution drags
in every upstream licence's obligations; fetching a component because the owner
asked for it, from its own publisher, does not. It also keeps the installer
small and lets a component be pinned and replaced without a release. Same
pattern as ripgrep and llama-server (``runtime/catalog.py``).

**No call happens before the owner agrees to the terms.** Once, in conversation,
recorded with the version they agreed to. If the terms change materially the
version changes and they are asked again — told what changed, not re-read the
whole thing.

The full text lives in ``docs/TELEPHONY_TERMS.md``. What is here is the spoken
summary and the gate.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: Bump only for a *material* change. Every bump re-asks every owner.
TERMS_VERSION = 1

#: What changed at each version, so a re-ask can say why.
TERMS_CHANGES: dict[int, str] = {
    1: "the first version of the phone terms",
}

DOC = "docs/TELEPHONY_TERMS.md"

#: The points that must actually be said aloud before a first call. Kept short
#: on purpose: terms nobody listens to protect nobody.
SPOKEN_POINTS: tuple[str, ...] = (
    "I am not an emergency service — never use me to call 911 or any emergency "
    "number, and keep a phone that works without me.",
    "I can get things wrong on a call, so check anything that matters. There is "
    "no warranty and no liability beyond the licence.",
    "Recording and AI-disclosure rules differ by country and by state, and "
    "following them is your call. I disclose that I am an assistant by default.",
    "Phone service and any calling app are your accounts with those companies, "
    "at their prices and under their terms.",
    "I will not claim to be human if asked, read out card numbers or one-time "
    "codes, or agree to a payment without you.",
)


def _path(home: Path | str | None = None) -> Path:
    base = Path(home or os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    d = base / "telephony"
    d.mkdir(parents=True, exist_ok=True)
    return d / "consent.json"


@dataclass(frozen=True, slots=True)
class Consent:
    version: int = 0
    at: str = ""

    @property
    def current(self) -> bool:
        return self.version >= TERMS_VERSION

    @property
    def stale(self) -> bool:
        """They agreed once, but to an older version."""
        return 0 < self.version < TERMS_VERSION


def read(home: Path | str | None = None) -> Consent:
    try:
        raw = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Consent()
    if not isinstance(raw, dict):
        return Consent()
    try:
        return Consent(version=int(raw.get("version") or 0), at=str(raw.get("at") or ""))
    except (TypeError, ValueError):
        return Consent()


def accept(home: Path | str | None = None) -> Consent:
    """Record agreement. Only ever called after the points were actually said."""
    consent = Consent(version=TERMS_VERSION, at=datetime.now(UTC).isoformat())
    _path(home).write_text(
        json.dumps({"version": consent.version, "at": consent.at, "doc": DOC}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    logger.info("telephony terms v%d accepted", consent.version)
    return consent


def withdraw(home: Path | str | None = None) -> None:
    """Take it back. Phone features stop until agreed again."""
    with contextlib.suppress(OSError):
        _path(home).unlink()


def ask(home: Path | str | None = None) -> str:
    """What she says before the first call is ever set up.

    A re-ask after a version bump leads with what changed, because reciting the
    whole thing again is how people learn to stop listening.
    """
    consent = read(home)
    if consent.current:
        return ""
    lead = "Before I can use a phone, a few things you should hear."
    if consent.stale:
        changed = TERMS_CHANGES.get(TERMS_VERSION, "the phone terms have changed")
        lead = f"The phone terms have changed — {changed}. Worth hearing again."
    points = "\n".join(f"- {p}" for p in SPOKEN_POINTS)
    return (
        f"{lead}\n{points}\n"
        f"The full version is in {DOC}. Are you happy for me to go ahead?"
    )


class TermsNotAcceptedError(RuntimeError):
    """Raised instead of dialling. Carries something sayable, not a code."""

    def __init__(self, home: Path | str | None = None) -> None:
        super().__init__(ask(home) or "The phone terms have not been agreed yet.")


def require(home: Path | str | None = None) -> None:
    """Gate every path that could put audio on a real line.

    Layered on the product-wide terms rather than replacing them: the general
    agreement covers "everything I do counts as you doing it", and this one adds
    what only applies once a machine can phone people. Agreeing to use a file
    manager is not agreeing to that.

    Deliberately not a soft warning: the bench is available without agreeing to
    anything, so there is no reason for a real call to slip through.
    """
    from remedy.core import terms

    terms.require(home)
    if not read(home).current:
        raise TermsNotAcceptedError(home)


# ---------------------------------------------------------------------------
# Nothing ships; everything is fetched on request
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Component:
    """Something Remedy will fetch only when the owner asks for it."""

    name: str
    purpose: str
    licence: str
    approx_mb: int
    #: Said before downloading, so "yes" means something.
    source: str

    def say(self) -> str:
        return (
            f"{self.name} — {self.purpose}. About {self.approx_mb} MB from "
            f"{self.source}, licensed {self.licence}."
        )


#: Declared here, fetched nowhere until asked. Mirrored in docs/THIRD_PARTY.md.
COMPONENTS: dict[str, Component] = {
    "baresip": Component(
        name="baresip",
        purpose="the SIP engine, for a phone number of my own",
        licence="BSD-3-Clause",
        approx_mb=6,
        source="the baresip project",
    ),
    "smart-turn": Component(
        name="smart-turn",
        purpose="knowing when someone has finished speaking, so I do not talk over them",
        licence="BSD-2-Clause",
        approx_mb=45,
        source="the Pipecat project",
    ),
    "chatterbox": Component(
        name="Chatterbox",
        purpose="a voice that does not sound synthetic on a phone line",
        licence="MIT",
        approx_mb=1100,
        source="Resemble AI",
    ),
    "android-image": Component(
        name="an Android system image",
        purpose="running a calling app here instead of on your phone",
        licence="its publisher's terms",
        approx_mb=2600,
        source="the Android-x86 or BlissOS project",
    ),
}


def offer_download(names: list[str]) -> str:
    """Say what is about to be downloaded before downloading it."""
    wanted = [COMPONENTS[n] for n in names if n in COMPONENTS]
    if not wanted:
        return ""
    total = sum(c.approx_mb for c in wanted)
    body = "\n".join(f"- {c.say()}" for c in wanted)
    return (
        f"That needs {len(wanted)} thing{'s' if len(wanted) > 1 else ''} I do not "
        f"ship with, about {total} MB in total:\n{body}\nShall I fetch them?"
    )
