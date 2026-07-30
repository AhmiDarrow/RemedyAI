"""L0 system replies — zero frontier tokens for status/identity/skills list."""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def try_l0_system_reply(
    runtime: Any,
    message: str,
    *,
    preclassified: bool = False,
) -> str | None:
    """Return a user-facing string if *message* is a high-confidence L0 ask.

    When the caller already classified as L0, pass ``preclassified=True`` to
    skip a redundant ``classify_turn_tier`` walk on the hot path.
    """
    from remedy.core.metabolism.tier import (
        TurnTier,
        classify_turn_tier,
        _L0_MODEL,
        _L0_SKILLS,
        _L0_STATUS,
        _L0_VERSION,
        _L0_WHOAMI,
    )

    msg = (message or "").strip()
    if not msg:
        return None
    # Re-confirm L0 unless the caller already did (early exit / agent gate)
    if not preclassified:
        tier = classify_turn_tier(msg, intent="chat", tools_enabled=False)
        if tier != TurnTier.L0_INSTANT:
            return None

    if _L0_VERSION.match(msg):
        ver = "unknown"
        with suppress(Exception):
            from importlib.metadata import version

            ver = version("remedy-ai")
        with suppress(Exception):
            import remedy

            ver = getattr(remedy, "__version__", ver) or ver
        return f"Remedy **{ver}** (local partner on this machine)."

    if _L0_MODEL.match(msg):
        provider = str(
            getattr(runtime, "_llm_provider", None)
            or getattr(getattr(runtime, "config", None), "llm_provider", None)
            or getattr(getattr(runtime, "config", None), "provider", None)
            or "—"
        )
        model = str(
            getattr(runtime, "_llm_model", None)
            or getattr(getattr(runtime, "config", None), "llm_model", None)
            or getattr(getattr(runtime, "config", None), "model", None)
            or "—"
        )
        with suppress(Exception):
            from remedy.core.llm_binding import get_llm_binding

            b = get_llm_binding(runtime)
            if b.provider:
                provider = b.provider
            if b.model:
                model = b.model
        return (
            f"**Provider:** {provider}\n"
            f"**Model:** {model}\n\n"
            "Continuity (memory, brief, skills) stays on this PC when you switch models."
        )

    if _L0_STATUS.match(msg):
        return (
            "Remedy is ready on this machine — local API + continuity active. "
            "Chat, tools, and workspace rails are available when configured."
        )

    if _L0_WHOAMI.match(msg):
        lines: list[str] = []
        with suppress(Exception):
            from remedy.memory.profile import load_profile

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            prof = load_profile(home)
            if isinstance(prof, dict):
                name = prof.get("display_name") or prof.get("name")
                if name:
                    lines.append(f"**Name:** {name}")
                facts = prof.get("facts") or []
                for f in list(facts)[:8]:
                    if isinstance(f, str) and f.strip():
                        lines.append(f"- {f.strip()[:200]}")
                    elif isinstance(f, dict):
                        t = str(f.get("text") or f.get("content") or "").strip()
                        if t:
                            lines.append(f"- {t[:200]}")
        with suppress(Exception):
            from remedy.core.metabolism.time_crystal import get_time_crystal

            sid = str(getattr(runtime, "_session_id", "") or "")
            block = get_time_crystal(sid).hot_block(max_chars=600)
            if block:
                lines.append(block)
        if not lines:
            return (
                "I do not have a durable profile yet. "
                "Tell me your name or say what to remember — or use `/remember …`."
            )
        return "**What I know about you**\n\n" + "\n".join(lines)

    if _L0_SKILLS.match(msg):
        names: list[str] = []
        with suppress(Exception):
            reg = getattr(runtime, "skills", None) or getattr(
                runtime, "skill_registry", None
            )
            if reg is not None:
                listing = getattr(reg, "list_skills", None) or getattr(
                    reg, "list", None
                )
                if callable(listing):
                    for s in listing() or []:
                        if isinstance(s, str):
                            names.append(s)
                        elif isinstance(s, dict) and s.get("name"):
                            names.append(str(s["name"]))
                        elif hasattr(s, "name"):
                            names.append(str(s.name))
        with suppress(Exception):
            from remedy.skills.shared import get_shared_registry

            reg = get_shared_registry()
            if reg is not None and not names:
                for s in getattr(reg, "skills", None) or []:
                    n = getattr(s, "name", None) or getattr(s, "id", None)
                    if n:
                        names.append(str(n))
                if hasattr(reg, "list_ids"):
                    names = list(reg.list_ids())[:40]
        names = list(dict.fromkeys(names))[:40]
        if not names:
            return (
                "No installed skills listed yet. Open **Skills** in the app "
                "to browse Library packs, or ask me to use a procedure once you install one."
            )
        body = "\n".join(f"- `{n}`" for n in names)
        return f"**Installed skills** ({len(names)}):\n\n{body}"

    return None
