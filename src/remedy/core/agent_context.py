"""Turn context assembly — workspace, Partner Memory, brief, skills catalog.

Extracted from ``BasicRuntime._build_context`` so the ReAct orchestrator stays
thin and this module can be typed under mypy independently.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


async def build_turn_context(runtime: Any) -> str:
    """Assemble system-context parts for the current agent turn.

    *runtime* is the live ``BasicRuntime`` (or compatible object) with
    ``memory``, ``config``, ``tool_registry``, ``skills``, project helpers.
    """
    from remedy.core.workspace import workspace_context_block

    parts: list[str] = []

    # Project workspace (default directory for this session)
    with suppress(Exception):
        parts.append(
            workspace_context_block(
                runtime.effective_project_path(),
                access_scope=runtime.access_scope(),
                extra_roots=runtime.allowed_roots(),
                project_unset=runtime.project_path_is_unset(),
            )
        )

    # Partner Memory (durable identity + preferences — default on, budget-capped)
    with suppress(Exception):
        if runtime.memory is not None:
            from remedy.memory.partner_memory import (
                build_partner_memory_block,
                reinforce_matching,
            )

            profile = await runtime.memory.get_or_create_profile()
            # Config user_name is the settings field; prefer live profile, fall back to config.
            if not (profile.display_name or "").strip():
                try:
                    from remedy.interfaces.config import load_config

                    user_name = str(load_config().get("user_name") or "").strip()
                    if user_name:
                        profile.display_name = user_name
                        await runtime.memory.save_user_profile(profile)
                except Exception:
                    pass
            # Prefer query-aware ranking when last user message is known
            q = str(getattr(runtime, "_last_user_text", "") or "")
            project_path = str(
                getattr(runtime.config, "project_path", None)
                or getattr(runtime, "_project_path", None)
                or ""
            ) or None
            # Light reinforce of matching facts (same session continuity)
            with suppress(Exception):
                if q and reinforce_matching(profile, q):
                    await runtime.memory.save_user_profile(profile)
            block = build_partner_memory_block(
                profile, query=q, project_path=project_path
            )
            if block:
                parts.append(block)
            # Full-scope reminder (no project jail) — once per context build
            if runtime.project_path_is_unset() or runtime.access_scope() == "full":
                parts.append(
                    "Access scope: full (no project folder). "
                    "Tools are not limited to a project jail — prefer "
                    "asking the user to pick a folder for focused coding, "
                    "and avoid broad writes outside the active task."
                )

    # Session Brief (Memory Harness L2) when present on agent
    with suppress(Exception):
        from remedy.memory.harness.brief import brief_to_context_block

        brief = getattr(runtime, "_session_brief", None)
        block = brief_to_context_block(brief)
        if block:
            parts.append(block)

    recent: list[Any] = []
    with suppress(Exception):
        # Keep short — large memory dumps push weak models into pointless tool loops.
        # Prefer query-time search later; recent is a light fallback.
        recent = await runtime.memory.list_recent(limit=6)
    if recent:
        lines = []
        for e in recent:
            content = (e.content or "").strip()
            # Skip noisy fallback/self-chat noise that poisons simple answers.
            if "fallback mode" in content.lower() or content.startswith("Received:"):
                continue
            if content.startswith("User (") or content.startswith("Remedy:"):
                # Gateway echo memories — skip; session history covers chat.
                continue
            ts = e.created_at.isoformat()[:19] if e.created_at else "?"
            lines.append(f"[{ts}] {content[:140]}")
        if lines:
            parts.append("Recent memory (optional):\n" + "\n".join(lines[-4:]))

    tools = runtime.tool_registry.tools
    if tools:
        names = ", ".join(t.name for t in tools)
        parts.append(f"Built-in tools (executable): {names}.")

    # Skills catalog (progressive disclosure stage 1) — ranked, not full bodies.
    with suppress(Exception):
        reg = getattr(runtime, "skills", None)
        count = int(getattr(reg, "count", 0) or 0) if reg is not None else 0
        if reg is not None and count > 0:
            ws = str(runtime.effective_project_path())
            # Single ranked catalog with workspace hint (no double rank / discard)
            ranked_lines = reg.summary_lines(limit=24, query="")
            if hasattr(reg, "match_skills"):
                top = reg.match_skills(
                    "",
                    limit=24,
                    workspace_hint=ws,
                )
                if top:
                    # Rebuild lines from ranked order with status badges
                    lines = []
                    for skill, _sc in top:
                        m = skill.manifest
                        st = (
                            m.status.value
                            if hasattr(m.status, "value")
                            else str(m.status)
                        )
                        desc = (m.description or "").strip()
                        if len(desc) > 140:
                            desc = desc[:137] + "…"
                        lines.append(f"- **{m.name}** [{st}]: {desc}")
                    lines.append(
                        "_Activate with skill_activate(name=…); rank with skill_search._"
                    )
                    ranked_lines = lines
            parts.append(
                "Skills catalog (name+status only — call skill_activate to load "
                "full procedure; skill_search to rank by task):\n"
                + "\n".join(ranked_lines)
            )
            with suppress(Exception):
                from remedy.core.metrics import default_registry

                default_registry.gauge("remedy_context_skills_listed").set(
                    float(min(count, 24))
                )
        else:
            parts.append(
                "Skills loaded: (none yet — bundled defaults load on server start)."
            )

    return "\n\n".join(parts)
