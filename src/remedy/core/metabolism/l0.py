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
        _L0_MODEL,
        _L0_SKILLS,
        _L0_STATUS,
        _L0_VERSION,
        _L0_WHOAMI,
        TurnTier,
        classify_turn_tier,
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
        with suppress(Exception):
            from remedy.memory.partner_memory import format_whoami

            prof = getattr(runtime, "_user_profile", None)
            if prof is not None:
                text = format_whoami(prof)
                with suppress(Exception):
                    from remedy.core.metabolism.time_crystal import get_time_crystal

                    sid = str(getattr(runtime, "_session_id", "") or "")
                    block = get_time_crystal(sid).hot_block(max_chars=600)
                    if block:
                        text = f"{text}\n\n{block}"
                return text
        lines: list[str] = []
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
        # Prefer human skill names (manifest.name). Never surface UUIDs.
        # Align with CLI `remedy skill list`: hide auto-learned probation by default.
        names: list[str] = []
        hidden_learned = 0

        def _skill_name(s: Any) -> str:
            if s is None:
                return ""
            if isinstance(s, str):
                return s.strip()
            if isinstance(s, dict):
                return str(s.get("name") or s.get("id") or "").strip()
            m = getattr(s, "manifest", None)
            if m is not None:
                n = getattr(m, "name", None)
                if n:
                    return str(n).strip()
            n = getattr(s, "name", None)
            if n:
                return str(n).strip()
            return ""

        def _is_learned_probation(s: Any) -> bool:
            meta = None
            st_v = ""
            if isinstance(s, dict):
                meta = s.get("metadata") or {}
                st_v = str(s.get("status") or "")
            else:
                m = getattr(s, "manifest", None)
                if m is not None:
                    meta = getattr(m, "metadata", None) or {}
                    st = getattr(m, "status", None)
                    st_v = (
                        st.value
                        if st is not None and hasattr(st, "value")
                        else str(st or "")
                    )
                else:
                    meta = getattr(s, "metadata", None) or {}
                    st_v = str(getattr(s, "status", "") or "")
            if not isinstance(meta, dict):
                meta = {}
            auto = bool(meta.get("auto_generated"))
            if not auto:
                return False
            return st_v not in ("active",)

        def _collect_from_reg(reg: Any) -> None:
            nonlocal hidden_learned
            if reg is None or names:
                return
            # Prefer iterable skills property, then list_skills()/list() helpers.
            skills_iter = None
            prop = getattr(reg, "skills", None)
            if prop is not None and not callable(prop):
                skills_iter = prop
            elif callable(prop):
                with suppress(Exception):
                    skills_iter = prop()
            if skills_iter is None:
                listing = getattr(reg, "list_skills", None) or getattr(reg, "list", None)
                if callable(listing):
                    with suppress(Exception):
                        skills_iter = listing()
            if not skills_iter:
                return
            for s in skills_iter or []:
                if _is_learned_probation(s):
                    hidden_learned += 1
                    continue
                n = _skill_name(s)
                # Drop UUID-like ids and empty labels
                if not n or (
                    len(n) >= 32
                    and n.count("-") >= 4
                    and all(c in "0123456789abcdef-" for c in n.lower())
                ):
                    continue
                names.append(n)

        with suppress(Exception):
            reg = getattr(runtime, "skills", None) or getattr(
                runtime, "skill_registry", None
            )
            _collect_from_reg(reg)
        with suppress(Exception):
            if not names:
                from remedy.skills.shared import get_shared_registry

                _collect_from_reg(get_shared_registry())
        # Stable, readable order; cap for chat density
        names = sorted(dict.fromkeys(names), key=str.lower)[:40]
        if not names:
            return (
                "No installed skills listed yet. Open **Skills** in the app "
                "to browse Library packs, or ask me to use a procedure once you install one."
            )
        body = "\n".join(f"- `{n}`" for n in names)
        tail = ""
        if hidden_learned:
            tail = (
                f"\n\n_{hidden_learned} auto-learned probation skill(s) hidden "
                f"(CLI: `remedy skill list --all`)._"
            )
        return f"**Installed skills** ({len(names)}):\n\n{body}{tail}"

    return None
