"""Change-safety / blast-radius standing guidance for coding turns.

Compact text is injected into tool/autonomous intent packs so Remedy checks
neighbors before multi-file edits — not only after ship gates. Full procedure
lives in the bundled **change-safety** skill (progressive disclosure).
"""

from __future__ import annotations

# Keep short: every coding turn pays this token cost.
CHANGE_SAFETY_SNIPPET = (
    "[Change-safety] Before multi-file edits or ship work: name the surface and "
    "blast radius (what else shares process, SPA, OS chrome, or messenger pollers). "
    "skill_activate(name=change-safety) for the checklist; skill_activate(name=project-etiquette) "
    "when shipping (test → docs → build → CI → publish). Green unit tests do not prove "
    "title bar, WebView2 browser, tray, or live Telegram — smoke those when touched. "
    "Prefer durable architecture over patch loops for known failure classes."
)


def change_safety_block(*, include: bool = True) -> str:
    """Return the standing snippet, or empty when disabled."""
    if not include:
        return ""
    return CHANGE_SAFETY_SNIPPET
