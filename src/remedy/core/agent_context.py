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

    # Soul Field — experimental personhood (opt-in maturity gate).
    # When disabled, still apply muscle profile for tool parallelism / builder addendum.
    with suppress(Exception):
        from remedy.core.feature_maturity import soul_field_enabled
        from remedy.core.llm_binding import get_llm_binding
        from remedy.core.muscle_profile import (
            apply_muscle_to_runtime,
            builder_system_addendum,
        )

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        bind = get_llm_binding(runtime)
        muscle = apply_muscle_to_runtime(runtime)
        if soul_field_enabled():
            from remedy.memory.soul.inject import build_soul_context_block

            user_name = ""
            with suppress(Exception):
                from remedy.interfaces.config import load_config

                user_name = str(load_config().get("user_name") or "").strip()
            soul_budget = 1800 if muscle.dense_memory else 1200
            lean = False
            with suppress(Exception):
                from remedy.core.react_policy import runtime_turn_is_chat_only

                lean = runtime_turn_is_chat_only(runtime)
            soul = build_soul_context_block(
                home=home,
                include_contract=not lean,
                provider=str(getattr(bind, "provider", "") or ""),
                model=str(getattr(bind, "model", "") or ""),
                user_name=user_name,
                max_chars=soul_budget,
                work_threads=not lean,
            )
            if soul:
                parts.append(soul)
        chat_only = False
        with suppress(Exception):
            from remedy.core.react_policy import runtime_turn_is_chat_only

            chat_only = runtime_turn_is_chat_only(runtime)

        # Capable muscle — skip the builder contract on greetings (it primes
        # a resume dump). Work turns still get the full RESEARCH→PLAN→BUILD.
        if not chat_only:
            build_add = builder_system_addendum(muscle)
            if build_add:
                parts.append(build_add)
        # Live build-engine phase (if this turn is a supervised construction)
        if not chat_only:
            with suppress(Exception):
                from remedy.core.build_engine import get_build_state
                from remedy.core.build_ledger import resume_hint

                bst = get_build_state(runtime)
                if bst is not None and bst.active:
                    parts.append(
                        f"[Build engine live] phase={bst.phase} "
                        f"explore={bst.explore_steps} write={bst.write_steps} "
                        f"verify={bst.verify_steps} "
                        f"verify_ok={bst.last_verify_ok} "
                        f"oracle={bst.verify_command or 'MISSING'} "
                        f"auto_verify={bst.auto_verify_ran} "
                        f"paths={', '.join(bst.paths_touched[-6:]) or '—'}"
                    )
                else:
                    # Mid-ship resume hint even when turn not yet classified as build
                    proj = ""
                    with suppress(Exception):
                        proj = str(runtime.effective_project_path() or "")
                    home = getattr(getattr(runtime, "config", None), "home_dir", None)
                    hint = resume_hint(proj or None, home=home)
                    if hint:
                        parts.append(hint)

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
            project_path = None
            with suppress(Exception):
                project_path = str(runtime.effective_project_path() or "") or None
            if not project_path:
                project_path = str(
                    getattr(runtime.config, "project_path", None)
                    or getattr(runtime, "_project_path", None)
                    or ""
                ) or None
            # Taste.json is a facet of the organism — fold into Partner Memory
            with suppress(Exception):
                from remedy.core.companion_taste import load_taste
                from remedy.memory.partner_memory import upsert_profile_fact

                dirty = False
                for row in load_taste(runtime)[:16]:
                    fact = str(row.get("fact") or "").strip()
                    if not fact:
                        continue
                    _uf, action = upsert_profile_fact(
                        profile,
                        fact,
                        category="design",
                        confidence=0.9,
                        source="taste",
                    )
                    if action in ("added", "reinforced"):
                        dirty = True
                if dirty:
                    await runtime.memory.save_user_profile(profile)
            # Light reinforce of matching facts (same session continuity)
            with suppress(Exception):
                if q and reinforce_matching(profile, q):
                    await runtime.memory.save_user_profile(profile)
            block = build_partner_memory_block(
                profile, query=q, project_path=project_path
            )
            if block:
                parts.append(block)
            # Recalled for *this* turn — FTS + facts the hot block didn't keep
            if q:
                with suppress(Exception):
                    from remedy.memory.living import format_turn_recall
                    from remedy.memory.partner_memory import search_partner_and_entries

                    hits = await search_partner_and_entries(
                        runtime.memory,
                        q,
                        limit=6,
                        project_path=project_path,
                    )
                    already = {
                        ln.split(") ", 1)[-1]
                        for ln in (block or "").splitlines()
                        if ln.startswith("- (")
                    }
                    recalled = format_turn_recall(hits, already=already)
                    if recalled:
                        parts.append(
                            "Recalled for this turn:\n" + "\n".join(recalled)
                        )
            # This-project chapter (decisions that survive session compress)
            with suppress(Exception):
                from remedy.core.project_learning import project_chapter_block

                chap = project_chapter_block(project_path, query=q)
                if chap:
                    parts.append(chap)
            # Prefer profile display name on soul (already injected) — keep profile
            # as source of truth for durable facts; soul carries dyadic residue.
            # Full-scope / no-focus reminder — optional focus, not a cage
            if runtime.project_path_is_unset() or runtime.access_scope() == "full":
                parts.append(
                    "Access scope: full (or no focus folder). "
                    "Tools may use absolute paths anywhere allowed for this account. "
                    "A focus folder is optional convenience for relative paths — "
                    "not required. Prefer reversible writes; confirm destructive ops."
                )
            with suppress(Exception):
                from remedy.core.approvals import APPROVALS, normalize_approval_mode

                am = normalize_approval_mode(APPROVALS.mode)
                if am == "full":
                    parts.append(
                        "Approvals: Full (warn). The host is in Remedy's hands. "
                        "Write and run anywhere this account can. Auth secrets "
                        "(~/.remedy/auth) stay closed. Do not stop to ask. "
                        "Do not call help_list. file_write / host_run / bash_exec now."
                    )
                elif am == "auto":
                    parts.append(
                        "Approvals: Auto (in-project). file_write / file_edit / pytest "
                        "/ uv / ruff inside the focus folder run without prompts. "
                        "OS, home, and sibling trees stay jailed unless the user "
                        "clicks Full."
                    )

    # Partner State dual streams (Phase D) — separate partner vs project budgets
    with suppress(Exception):
        from remedy.memory.partner_state import partner_context_blocks

        for block in partner_context_blocks(runtime):
            if block:
                parts.append(block)

    # Session Brief (Memory Harness L2) when present on agent
    chat_only_ctx = False
    with suppress(Exception):
        from remedy.core.react_policy import runtime_turn_is_chat_only

        chat_only_ctx = runtime_turn_is_chat_only(runtime)
    if not chat_only_ctx:
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

        # Continuity steering — open tasks / soul threads / mid-ship (anti-thrash)
        with suppress(Exception):
            from remedy.core.continuity_steering import continuity_steering_block

            home_cs = getattr(getattr(runtime, "config", None), "home_dir", None)
            cs = continuity_steering_block(runtime, home=home_cs, max_chars=900)
            if cs:
                parts.append(cs)

    # Messenger origin — same partner, remote surface
    with suppress(Exception):
        origin = str(getattr(runtime, "_origin_channel", "") or "").strip()
        if not origin:
            # Session-bound messenger chats (msg:telegram:…)
            sid = str(getattr(runtime, "_session_id", "") or "")
            if sid.startswith("msg:") and ":" in sid[4:]:
                origin = sid.split(":", 2)[1]
        if origin:
            parts.append(
                f"[Messenger surface: {origin}] "
                "You are the same Remedy as desktop chat for this person. "
                "Keep replies concise for chat apps; still use tools when work needs them. "
                "Do not introduce yourself as a new bot."
            )

    # Machine-native working memory: project the middleman slice for the current
    # query. Holds tool results + facts the brief / recent-memory do not, keyed
    # by what this turn is about (not recency), budget-bounded for small windows.
    with suppress(Exception):
        from remedy.core.llm_binding import get_llm_binding
        from remedy.nanoswarm.token_nanobot import resolve_context_window

        _bind = get_llm_binding(runtime)
        _win = int(resolve_context_window(_bind.provider, _bind.model))
        # Local RMB: larger middleman slice (facts/tool results) for long coding sessions
        _frac = 0.15
        try:
            from remedy.runtime.rmb.mode import is_rmb_provider

            if is_rmb_provider(_bind.provider, getattr(_bind, "base_url", None)):
                _frac = 0.22
        except Exception:
            pass
        _budget = max(180, int(_win * _frac))
        _query = str(getattr(runtime, "_last_user_text", "") or "")
        _paths = []
        _brief = getattr(runtime, "_session_brief", None)
        if _brief is not None:
            _paths = list(getattr(_brief, "key_paths", None) or [])[:8]
        _block = _middleman_context_block(runtime, _query, _paths, _budget)
        if _block:
            parts.append(_block)

    # RMB system addendum: harness + tools + endless session contract
    with suppress(Exception):
        from remedy.core.llm_binding import get_llm_binding
        from remedy.runtime.rmb.mode import is_rmb_provider

        _b = get_llm_binding(runtime)
        if is_rmb_provider(_b.provider, getattr(_b, "base_url", None)):
            parts.append(
                "RMB local agent: You are the sole on-device model for this session. "
                "No separate vision stack — image attachments are file paths; use tools "
                "to inspect them. Context is managed automatically (Session Brief, prune, "
                "offload) — do not discuss memory pressure or ask the user to compress. "
                "Finish tool chains, keep working, prefer compact tool results and "
                "concrete paths."
            )

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
                        "self-inject",
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
                # Local/RMB windows cannot afford multi-k procedure bodies — the
                # model should skill_activate on demand; endless_context drops
                # these first when still over budget.
                # Full skill bodies for cloud/Grok; modest cap only on tiny local windows
                _PROC_CAP = 48_000
                try:
                    from remedy.core.llm_binding import get_llm_binding as _glb
                    from remedy.nanoswarm.token_nanobot import is_local_model as _is_loc

                    _b_loc = _glb(runtime)
                    if _is_loc(
                        _b_loc.provider, _b_loc.model, base_url=_b_loc.base_url
                    ):
                        _PROC_CAP = 4_000
                except Exception:
                    pass
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

    # Small-model context budget. A 4k–8k local window cannot carry the full
    # always-on block (static instructions + skills catalog ≈3k tokens) plus any
    # history or answer. The Memory Harness only ever prunes the *history*, never
    # this untouchable head, so trim the head itself for constrained windows or
    # the model is truncated mid-prompt on the very first turn.
    try:
        from remedy.core.llm_binding import get_llm_binding
        from remedy.nanoswarm.token_nanobot import resolve_context_window

        bind = get_llm_binding(runtime)
        window = int(
            resolve_context_window(
                bind.provider, bind.model, base_url=bind.base_url
            )
        )
        # Local/RMB: leave more room for tools + answer (head was 45% of a wrong
        # 128k window before; with a real 4–6k window, 35% keeps tools alive).
        from remedy.nanoswarm.token_nanobot import is_local_model

        # Local: head must leave room for tool schemas + completion.
        # 0.28 of 32k ≈ 9k head max; coding tools still need ~2–6k.
        _local = is_local_model(bind.provider, bind.model, base_url=bind.base_url)
        head_frac = 0.28 if _local else 0.45
        budget = int(window * head_frac)
        # Prefer token estimate when available (chars was too loose for catalogs).
        try:
            from remedy.nanoswarm.token_nanobot import estimate_text_tokens

            joined_est = estimate_text_tokens(
                "\n\n".join(parts),
                provider=bind.provider,
                model=bind.model,
            )
            over = budget >= 512 and joined_est > budget
        except Exception:
            over = budget >= 512 and budget < len("\n\n".join(parts))
        if over:
            parts = _trim_context_parts(
                parts, budget, provider=bind.provider, model=bind.model
            )
    except Exception:
        pass

    return "\n\n".join(parts)


def _middleman_context_block(
    runtime: Any, query: str, paths: list[str], budget: int
) -> str:
    """Project the relevant middleman slice for this turn, or '' when empty."""
    from remedy.memory.middleman import get_session_middleman

    sid = str(getattr(runtime, "_session_id", None) or "")
    if not sid:
        return ""
    # Do not filter by session_id: this store is already the session hot set
    # plus eternal facts/life hydrated from CAS. A session filter would drop
    # the cross-session objects the machine is supposed to recall.
    proj = get_session_middleman(sid).project(
        query,
        budget_tokens=budget,
        paths=paths or None,
    )
    if not proj:
        return ""
    return "Working memory (retrieved by query):\n" + proj


def _trim_context_parts(
    parts: list[str],
    budget: int,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> list[str]:
    """Drop the most expendable always-on blocks until the head fits the window.

    Keeps orientation / workspace / partner / brief and drops, in order: the
    skills catalog, auto-suggested skill bodies, then the secondary instruction
    blocks. Never removes the isolation banner or workspace root.
    """
    from remedy.nanoswarm.token_nanobot import estimate_text_tokens

    def _est(text: str) -> int:
        return int(
            estimate_text_tokens(text, provider=provider or None, model=model or None)
        )

    # Lower priority = dropped first. Index-based so repeats are unambiguous.
    def _priority(text: str) -> int:
        if "Skills catalog" in text or "[Skill auto-suggest]" in text:
            return 0
        if text.startswith(
            (
                "Self-configuration:",
                "Durable memory:",
                "Owner's manual",
                "Recent memory",
                "Built-in tools",
                "Working memory (retrieved by query):",
            )
        ):
            return 1
        return 2

    out = list(parts)
    while len(out) > 1:
        total = _est("\n\n".join(out))
        if total <= budget:
            break
        # Drop the single lowest-priority part (ties: largest first).
        drop_idx = 0
        drop_pri = 99
        drop_size = -1
        for i, t in enumerate(out):
            pri = _priority(t)
            size = len(t)
            if pri < drop_pri or (pri == drop_pri and size > drop_size):
                drop_idx, drop_pri, drop_size = i, pri, size
        if drop_pri == 2:
            break  # only must-keep blocks remain
        out.pop(drop_idx)
    # If a lone oversized secondary block still exceeds budget, hard-truncate it.
    total = _est("\n\n".join(out))
    if total > budget:
        for i, t in enumerate(out):
            if _priority(t) == 1 and len(out) > 1:
                out.pop(i)
                break
    return out
