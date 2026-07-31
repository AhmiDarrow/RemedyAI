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

    # Hard isolation banner — model must not continue other tabs' work
    with suppress(Exception):
        from remedy.core.session_continuity import session_isolation_system_line

        iso = session_isolation_system_line(runtime)
        if iso:
            parts.append(iso)

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

    # Orientation + fingerprint for focus and active work roots (session-touched trees).
    with suppress(Exception):
        from remedy.core.work_roots import work_roots_context_block

        wr = work_roots_context_block(runtime)
        if wr:
            parts.append(wr)
        else:
            from remedy.core.project_fingerprint import fingerprint_path, orientation_block

            focus = runtime.effective_project_path()
            orient = orientation_block(focus)
            if orient:
                parts.append(orient)
            fp = fingerprint_path(focus)
            fp_lines = fp.context_lines()
            if fp_lines:
                parts.append("\n".join(fp_lines))

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
            # Full-scope / no-focus reminder — optional focus, not a cage
            if runtime.project_path_is_unset() or runtime.access_scope() == "full":
                parts.append(
                    "Access scope: full (or no focus folder). "
                    "Tools may use absolute paths anywhere allowed for this account. "
                    "A focus folder is optional convenience for relative paths — "
                    "not required. Prefer reversible writes; confirm destructive ops."
                )

    # Partner State dual streams (Phase D) — separate partner vs project budgets
    with suppress(Exception):
        from remedy.memory.partner_state import partner_context_blocks

        for block in partner_context_blocks(runtime):
            if block:
                parts.append(block)

    # Session Brief (Memory Harness L2) when present on agent
    with suppress(Exception):
        from remedy.memory.harness.brief import brief_to_context_block
        from remedy.memory.partner_state import ensure_partner_state

        brief = getattr(runtime, "_session_brief", None)
        # Phase C: project epistemic graph → brief before inject
        with suppress(Exception):
            ensure_partner_state(runtime).apply_graph_to_brief(brief)
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

    # Self-setup: user can ask Remedy to configure itself in chat
    parts.append(
        "Self-configuration: when the user asks you to set up, enable, disable, "
        "change, or configure Remedy (web tools, approval mode, model/provider, "
        "vision, persona, their name, project folder, access scope, messengers, "
        "assistant prefs, etc.), call update_settings (or get_settings first). "
        "Apply the change yourself — do not only point them at Settings UI. "
        "Examples: update_settings(setup=\"web tools\"), "
        "update_settings(approval_mode=\"auto\"), "
        "update_settings(user_name=\"…\", thinking_level=\"medium\")."
    )
    parts.append(
        "Durable memory: when the user says remember / note that / don't forget / "
        "store in memory, ALWAYS call memory_save(content=…) with the fact "
        "(in addition to any automatic silent save). Confirm briefly what was stored. "
        "Never store secrets or API keys."
    )
    parts.append(
        "Owner's manual / F1 Help: you CAN and SHOULD read it. Call help_list to "
        "see article ids (same chapters as in-app F1), then help_read(id=…) for the "
        "full markdown (e.g. computer-use-soak, 19-metabolism, 00-overview). "
        "Never claim F1/help is outside access scope — help_read bypasses project "
        "jail for these read-only manuals. file_read on absolute help paths also works."
    )

    # Skills catalog (progressive disclosure stage 1) — ranked, not full bodies.
    # Prefer warm rank cache from speculative prep / prior turns (skip re-rank).
    # Concrete tasks ("review project") re-rank and may auto-inject the top
    # procedure so the model has a real checklist without a skill_activate hop.
    with suppress(Exception):
        reg = getattr(runtime, "skills", None)
        count = int(getattr(reg, "count", 0) or 0) if reg is not None else 0
        if reg is not None and count > 0:
            import re as _re

            ranked_lines: list[str] = []
            used_warm = False
            task_q = ""
            top_ranked: list[tuple[Any, float]] = []
            with suppress(Exception):
                task_q = str(
                    getattr(runtime, "_turn_user_text", None)
                    or getattr(runtime, "_last_user_text", None)
                    or ""
                ).strip()[:240]
            # Greets / one-word acks are not skill-ranking queries — keep warm cache.
            if task_q and (
                len(task_q) < 4
                or task_q.lower().rstrip("!.?")
                in {
                    "hi",
                    "hey",
                    "hello",
                    "thanks",
                    "thank you",
                    "ok",
                    "okay",
                    "yes",
                    "no",
                    "yep",
                    "nope",
                    "sure",
                    "cool",
                    "bye",
                }
            ):
                task_q = ""
            with suppress(Exception):
                from remedy.nanoswarm import get_swarm

                warm = list(getattr(get_swarm().skill, "_rank_cache", None) or [])
                # Warm is usable for pure chat / empty query; for concrete tasks
                # re-rank so "review project" surfaces the right skills.
                if len(warm) >= 3 and not task_q:
                    ranked_lines = warm[:24]
                    used_warm = True
            if not ranked_lines:
                ws = str(runtime.effective_project_path())
                # One rank pass with workspace + task query
                if hasattr(reg, "match_skills"):
                    top_ranked = list(
                        reg.match_skills(
                            task_q,
                            limit=24,
                            workspace_hint=ws,
                        )
                        or []
                    )
                    if top_ranked:
                        lines = []
                        for skill, _sc in top_ranked:
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
                if not ranked_lines and hasattr(reg, "summary_lines"):
                    ranked_lines = list(reg.summary_lines(limit=24, query="") or [])
                # Seed warm cache for next turn / library chip path
                if ranked_lines:
                    with suppress(Exception):
                        from remedy.nanoswarm import get_swarm

                        get_swarm().skill._rank_cache = list(ranked_lines)
            parts.append(
                "Skills catalog (name+status only — call skill_activate to load "
                "ONE full procedure for the current task; skill_search to rank; "
                "skill_reload to rescan disk — never skill_activate every pack):\n"
                + "\n".join(ranked_lines)
            )
            # Auto-suggest: review/coding tasks → inject preferred procedure body
            # (stage-2 progressive disclosure without waiting for a skill_activate hop).
            # "review project" must surface change-safety even when token overlap is modest.
            with suppress(Exception):
                tq = (task_q or "").lower()
                preferred: list[str] = []
                if _re.search(
                    r"\b(review|audit|blast.?radius|neighbors|code review)\b", tq
                ):
                    preferred = [
                        "change-safety",
                        "project-etiquette",
                        "refactor-safe",
                    ]
                elif _re.search(r"\b(ship|release|publish|etiquette|ci|pypi)\b", tq):
                    preferred = ["project-etiquette", "change-safety"]
                elif _re.search(
                    r"\b(dogfood|self-?dev|isolated dev|work on herself|gauntlet|"
                    r"product soak|stress suite|red-?team)\b",
                    tq,
                ):
                    preferred = [
                        "self-dev-loop",
                        "dogfood-isolated",
                        "gauntlet-security",
                        "soak-product",
                        "stress-suite",
                    ]
                elif _re.search(r"\b(refactor)\b", tq):
                    preferred = ["refactor-safe", "change-safety"]
                elif _re.search(
                    r"\b(implement|fix|debug|multi-?file|codebase)\b", tq
                ):
                    preferred = ["change-safety", "refactor-safe"]
                # Cap keeps one skill from dominating (change-safety ~3.5k).
                _PROC_CAP = 4_000
                _GENERIC_MIN = 0.48
                _PREFERRED_MIN = 0.18
                pick_skill = None
                pick_score = 0.0
                ranked_map = {
                    str(s.manifest.name): (s, float(sc)) for s, sc in top_ranked
                }
                for name in preferred:
                    hit = ranked_map.get(name)
                    if hit is not None and hit[1] >= _PREFERRED_MIN:
                        pick_skill, pick_score = hit
                        break
                    # Preferred skill installed but weak rank — still inject for
                    # clear review/coding asks (SO leap for "review project").
                    if hasattr(reg, "get"):
                        sk = reg.get(name)
                        if sk is not None:
                            pick_skill, pick_score = sk, max(
                                _PREFERRED_MIN, float(hit[1]) if hit else 0.5
                            )
                            break
                if pick_skill is None and top_ranked:
                    sk0, sc0 = top_ranked[0]
                    if float(sc0) >= _GENERIC_MIN:
                        pick_skill, pick_score = sk0, float(sc0)
                if pick_skill is not None:
                    m = pick_skill.manifest
                    meta = m.metadata or {}
                    st = (
                        m.status.value
                        if hasattr(m.status, "value")
                        else str(m.status)
                    )
                    injectable = not meta.get("quarantine") and str(st).lower() not in (
                        "disabled",
                        "archived",
                        "deprecated",
                    )
                    if injectable:
                        body = (
                            getattr(pick_skill, "instructions", None) or ""
                        ).strip()
                        if not body and hasattr(reg, "skill_body"):
                            body = str(reg.skill_body(m.name) or "").strip()
                        if body:
                            if len(body) > _PROC_CAP:
                                body = (
                                    body[:_PROC_CAP]
                                    + f"\n\n…[auto-suggest truncated at {_PROC_CAP} chars"
                                    " — skill_activate for full procedure]"
                                )
                            parts.append(
                                f"[Skill auto-suggest] Top match for this task: "
                                f"**{m.name}** (score={float(pick_score):.2f}). "
                                "Procedure loaded into context — follow it; "
                                f"skill_activate(name={m.name}) only if you need "
                                "references or a refresh.\n\n"
                                f"{body}"
                            )
                            with suppress(Exception):
                                from remedy.core.metrics import default_registry

                                default_registry.counter(
                                    "remedy_skill_auto_suggest_inject_total"
                                ).inc()
                            with suppress(Exception):
                                if hasattr(reg, "mark_activated"):
                                    reg.mark_activated(m.name)
            with suppress(Exception):
                from remedy.core.metrics import default_registry

                default_registry.gauge("remedy_context_skills_listed").set(
                    float(min(count, 24))
                )
                if used_warm:
                    default_registry.counter("remedy_skills_catalog_warm_hit").inc()
                else:
                    default_registry.counter("remedy_skills_catalog_warm_miss").inc()
        else:
            parts.append(
                "Skills loaded: (none yet — bundled defaults load on server start)."
            )

    return "\n\n".join(parts)
