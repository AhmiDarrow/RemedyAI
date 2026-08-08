"""Continuity steering — keep open work visible so models do not thrash.

Injects a short, high-priority block: Session Brief open tasks, soul threads,
and mid-ship build resume. Complements organism pulse (mood/metabolism) with
*what we are still doing together*.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def continuity_steering_block(
    runtime: Any = None,
    *,
    home: Any = None,
    max_chars: int = 900,
) -> str:
    """Return a system inject block, or empty if nothing open."""
    lines: list[str] = []
    open_tasks: list[str] = []
    next_steps: list[str] = []
    intent = ""
    key_paths: list[str] = []
    constraints: list[str] = []

    brief = None
    with suppress(Exception):
        brief = getattr(runtime, "_session_brief", None)
    if brief is not None:
        with suppress(Exception):
            intent = str(getattr(brief, "intent", "") or "").strip()
            open_tasks = [
                str(t).strip()
                for t in (getattr(brief, "open_tasks", None) or [])
                if str(t).strip()
            ][:8]
            next_steps = [
                str(t).strip()
                for t in (getattr(brief, "next_steps", None) or [])
                if str(t).strip()
            ][:6]
            key_paths = [
                str(p).strip()
                for p in (getattr(brief, "key_paths", None) or [])
                if str(p).strip()
            ][:8]
            constraints = [
                str(c).strip()
                for c in (getattr(brief, "user_constraints", None) or [])
                if str(c).strip()
            ][:5]

    soul_threads: list[str] = []
    with suppress(Exception):
        from remedy.core.feature_maturity import soul_field_enabled
        from remedy.memory.soul.field import load_soul_field

        if soul_field_enabled():
            h = home
            if h is None and runtime is not None:
                h = getattr(getattr(runtime, "config", None), "home_dir", None)
            sf = load_soul_field(h)
            soul_threads = [
                str(t).strip()
                for t in (sf.relational.open_threads or [])
                if str(t).strip()
            ][:5]

    build_resume = ""
    with suppress(Exception):
        from remedy.core.build_ledger import resume_hint

        proj = ""
        if runtime is not None:
            with suppress(Exception):
                proj = str(runtime.effective_project_path() or "")
        h = home
        if h is None and runtime is not None:
            h = getattr(getattr(runtime, "config", None), "home_dir", None)
        build_resume = resume_hint(proj or None, home=h) or ""

    if not (
        intent
        or open_tasks
        or next_steps
        or soul_threads
        or build_resume
        or constraints
    ):
        return ""

    lines.append("[Continuity — do not reset or monologue past this work]")
    if intent:
        lines.append(f"Intent: {intent[:200]}")
    if open_tasks:
        lines.append("Open tasks:")
        for t in open_tasks:
            lines.append(f"  - {t[:160]}")
    if next_steps:
        lines.append("Next steps:")
        for t in next_steps:
            lines.append(f"  - {t[:160]}")
    if soul_threads:
        lines.append("Relational open threads:")
        for t in soul_threads:
            lines.append(f"  - {t[:160]}")
    if constraints:
        lines.append("User constraints (honor):")
        for c in constraints:
            lines.append(f"  - {c[:140]}")
    if key_paths:
        lines.append("Key paths: " + ", ".join(key_paths[:8]))
    if build_resume:
        lines.append(build_resume)
    lines.append(
        "Rules: Prefer tools over prose when work remains. "
        "Do not claim done without verify evidence. "
        "Resume — do not restart from zero."
    )

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
