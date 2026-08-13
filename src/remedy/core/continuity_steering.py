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
    future_dreams_from_soul: list[str] = []
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
            future_dreams_from_soul = [
                str(d).strip()
                for d in (getattr(sf, "future_dreams", None) or [])
                if str(d).strip()
            ][:3]

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

    # Runtime goals (organism checklist — not only this chat's brief)
    runtime_goals: list[str] = []
    with suppress(Exception):
        if runtime is not None and hasattr(runtime, "list_tasks"):
            from remedy.models import TaskStatus

            for t in list(runtime.list_tasks() or [])[:16]:
                if "goal" not in (getattr(t, "tags", None) or []):
                    continue
                st = getattr(t, "status", None)
                if st == TaskStatus.COMPLETED:
                    continue
                title = str(getattr(t, "title", "") or "").strip()
                if title and title not in open_tasks:
                    runtime_goals.append(title[:160])
            runtime_goals = runtime_goals[:5]

    # Life & goals the organism already knows (global partner facts)
    life_lines: list[str] = []
    with suppress(Exception):
        prof = None
        if runtime is not None:
            prof = getattr(runtime, "_user_profile", None) or getattr(
                runtime, "user_profile", None
            )
        mem = getattr(runtime, "memory", None) if runtime is not None else None
        if prof is None and mem is not None:
            prof = getattr(mem, "profile", None) or getattr(mem, "_profile", None)
        if prof is not None:
            from remedy.memory.living import life_goal_lines

            life_lines = life_goal_lines(prof, limit=3)

    future_dreams: list[str] = []
    with suppress(Exception):
        future_dreams = list(future_dreams_from_soul)

    if not (
        intent
        or open_tasks
        or next_steps
        or soul_threads
        or build_resume
        or constraints
        or runtime_goals
        or life_lines
        or future_dreams
    ):
        return ""

    lines.append("[Continuity — do not reset or monologue past this work]")
    if intent:
        lines.append(f"Intent: {intent[:200]}")
    if open_tasks:
        lines.append("Open tasks:")
        for t in open_tasks:
            lines.append(f"  - {t[:160]}")
    if runtime_goals:
        lines.append("Open goals:")
        for t in runtime_goals:
            lines.append(f"  - {t[:160]}")
    if life_lines:
        lines.append("Life & goals (honor — this is the person, not only the repo):")
        for t in life_lines:
            lines.append(f"  - {t[:160]}")
    if future_dreams:
        lines.append("Dreams of the future (partner toward their goals):")
        for t in future_dreams:
            lines.append(f"  - {t[:180]}")
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
