"""Build supervisor I would run: write, smallest test, one repair card.

Older agent loops (phase essays, forced TDD, background hops while the live
model is already editing) fight a capable model. They burn context and write
files the turn did not ask for.

This module is the opposite:

1. The live model edits the tree.
2. After the first source write, run the smallest falsifier we have.
3. On red, inject **one** card: read this file, edit, re-run these nodes.
4. Background TDD / isolated hops stay off unless the user left the turn
   unattended (away_mode) or called ``build_drive`` themselves.
"""

from __future__ import annotations

from typing import Any


def allow_background_drive(state: Any) -> bool:
    """True only when the user is not at the keyboard (unattended)."""
    if state is None or not getattr(state, "active", False):
        return False
    return bool(getattr(state, "away_mode", False))


def collapse_to_one_card(
    messages: list[dict[str, Any]],
    card: dict[str, str] | None,
) -> bool:
    """Append *card* if this batch has no build-engine inject yet.

    Returns True when the card was added. Build-engine injects all start with
    ``[Build engine``.
    """
    if not card or not isinstance(card, dict):
        return False
    body = str(card.get("content") or "")
    if not body.strip():
        return False
    for m in reversed(messages[-8:]):
        if not isinstance(m, dict):
            continue
        if str(m.get("content") or "").lstrip().startswith("[Build engine"):
            return False
    messages.append(card)
    return True
