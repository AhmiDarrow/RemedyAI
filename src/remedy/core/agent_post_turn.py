"""Post-turn continuity hooks extracted from BasicRuntime.

Keeps project learning + speculative prep off the ReAct stream body so the
orchestrator only schedules work after a turn completes (or mid-tool warm).
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def schedule_post_turn_prep(
    runtime: Any,
    *,
    message: str = "",
    session_id: str | None = None,
) -> None:
    """Warm brief/memory/skills and lightly record project profile.

    Safe to call from stream finally-paths; never raises to the caller.
    """
    with suppress(Exception):
        from remedy.core.project_learning import record_session_end
        from remedy.core.session_quality import get_session_quality
        from remedy.core.speculative import schedule_speculative_prep

        sid = str(session_id or getattr(runtime, "_session_id", None) or "")
        qsnap = get_session_quality(sid).snapshot()
        project_path = None
        with suppress(Exception):
            project_path = str(runtime.effective_project_path() or "") or None
        if not project_path:
            project_path = (
                str(
                    getattr(getattr(runtime, "config", None), "project_path", None)
                    or getattr(runtime, "_project_path", None)
                    or ""
                )
                or None
            )
        # Light touch each turn (not only true session end)
        if project_path and int(qsnap.get("turns") or 0) > 0:
            # Only merge full profile every few turns to limit disk IO
            if int(qsnap.get("turns") or 0) % 5 == 0:
                record_session_end(project_path, qsnap)
        schedule_speculative_prep(
            session_id=sid,
            brief=getattr(runtime, "_session_brief", None),
            messages=getattr(runtime, "_last_send_messages", None),
            user_text=message or "",
            project_path=project_path,
            memory=getattr(runtime, "memory", None),
        )
        # Metabolism: finish IR, promote Time Crystal, optional critical verify
        with suppress(Exception):
            from remedy.core.metabolism.cua_macros import get_cua_macros
            from remedy.core.metabolism.skill_genome import get_skill_genome
            from remedy.core.metabolism.turn import end_turn_metabolism
            from remedy.core.turn_context import current_turn_tool_steps

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            recent_tools: list[str] = []
            with suppress(Exception):
                steps = current_turn_tool_steps(runtime)
                if isinstance(steps, list):
                    for s in steps[-12:]:
                        if isinstance(s, dict):
                            recent_tools.append(
                                f"{s.get('tool')}: {s.get('result') or s.get('error') or ''}"[:500]
                            )
            asst = str(getattr(runtime, "_last_assistant_text", "") or "")
            from remedy.core.turn_context import (
                set_turn_action_ir,
                stash_pending_verify_remedy,
                turn_action_ir,
                turn_metabolism_allow_verify,
                turn_tier,
            )

            end = end_turn_metabolism(
                session_id=sid,
                action_ir=turn_action_ir(runtime),
                status="done",
                assistant_text=asst,
                user_text=message or "",
                recent_tool_texts=recent_tools,
                allow_verify=bool(
                    turn_metabolism_allow_verify(runtime) or int(turn_tier(runtime)) >= 2
                ),
                home=home,
            )
            # If verify failed, stash silent remedy for next turn of THIS session
            if end.get("verify_remedy"):
                stash_pending_verify_remedy(sid, end["verify_remedy"])
            with suppress(Exception):
                get_cua_macros().persist(home)
                get_skill_genome().persist(home)
            set_turn_action_ir(None, runtime)
            # Soul Field micro-update — personhood residue across providers
            soul_on = True
            with suppress(Exception):
                from remedy.core.feature_maturity import soul_field_enabled

                soul_on = bool(soul_field_enabled())
            if soul_on:
                with suppress(Exception):
                    import asyncio
                    import threading

                    from remedy.core.turn_context import turn_session_id
                    from remedy.memory.consolidator import MemoryConsolidator

                    mem = getattr(runtime, "memory", None)
                    sid_c = str(turn_session_id(runtime) or sid or "")
                    if mem is not None and sid_c and not sid_c.startswith("hive_"):

                        def _consolidate() -> None:
                            # Swallow: tests and closed stores raise
                            # "MemoryStore not initialized"; a daemon thread
                            # must never leak that to the owner process.
                            try:

                                async def _go() -> None:
                                    notes = await mem.list_by_session(sid_c, limit=24)
                                    if len(notes) >= 8:
                                        await MemoryConsolidator(mem).consolidate_session(
                                            sid_c
                                        )

                                asyncio.run(_go())
                            except Exception:
                                return

                        threading.Thread(
                            target=_consolidate,
                            daemon=True,
                            name="remedy-consolidate",
                        ).start()
                with suppress(Exception):
                    from remedy.core.llm_binding import get_llm_binding
                    from remedy.core.turn_context import turn_session_id
                    from remedy.memory.soul.update import update_soul_after_turn

                    bind = get_llm_binding(runtime)
                    update_soul_after_turn(
                        user_text=message or "",
                        assistant_text=asst,
                        session_id=str(turn_session_id(runtime) or sid or ""),
                        provider=str(getattr(bind, "provider", "") or ""),
                        model=str(getattr(bind, "model", "") or ""),
                        brief=getattr(runtime, "_session_brief", None),
                        project_path=project_path or "",
                        home=home,
                    )
                # Occasional dream densification (cooldown inside dream_cycle)
                with suppress(Exception):
                    from remedy.memory.soul.dream import dream_cycle, should_dream
                    from remedy.memory.soul.field import load_soul_field

                    sf = load_soul_field(home)
                    ready = len(sf.episodes) >= 3 or bool(sf.pledges) or bool(sf.future_dreams)
                    if ready and should_dream(home):
                        # Off the request task so SSE finally can release the
                        # stream claim — a follow-up send must not 409.
                        import threading

                        threading.Thread(
                            target=dream_cycle,
                            kwargs={
                                "home": home,
                                "memory": getattr(runtime, "memory", None),
                                "field": sf,
                            },
                            daemon=True,
                            name="remedy-dream",
                        ).start()
                # Soft arm mission from soul when idle-ish (no active mission)
                with suppress(Exception):
                    from remedy.memory.soul.field import load_soul_field
                    from remedy.memory.soul.missions_bridge import arm_soul_missions

                    sf2 = load_soul_field(home)
                    if sf2.relational.turns_together > 0 and sf2.relational.turns_together % 8 == 0:
                        arm_soul_missions(runtime, home=home, max_new=1, auto=True)
            # Always refresh somatic signal for tray / status bar (organism visible)
            with suppress(Exception):
                from remedy.core.metabolism.organism import (
                    apply_soma_to_vitals,
                    collect_vitals,
                    load_vitals,
                    persist_vitals,
                )
                from remedy.core.muscle_profile import muscle_from_runtime
                from remedy.memory.soul.somatic import refresh_soma

                m = muscle_from_runtime(runtime)
                pub = refresh_soma(
                    home,
                    muscle_label=m.label,
                    muscle_provider=m.provider,
                )
                prev = load_vitals(home)
                if prev.get("alive"):
                    apply_soma_to_vitals(pub, home)
                else:
                    persist_vitals(collect_vitals(home, runtime=runtime), home)
            # Realtime skill lifecycle: promote/demote/prune from this turn's stats
            with suppress(Exception):
                getter = getattr(runtime, "_get_learning_loop", None)
                ll = getter() if callable(getter) else None
                if ll is not None and hasattr(ll, "tick_learned_skills"):
                    ll.tick_learned_skills()
            runtime._last_assistant_text = ""


def distill_user_message_now(
    runtime: Any,
    message: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Synchronous Partner Memory distill for the current user line.

    Called at turn start for explicit “remember …” so facts are available
    before the model replies (background speculative can race).

    Belt-and-suspenders: also runs the ``memory_save`` tool handler on the
    extracted fact so FTS + profile stay aligned even if the model never
    calls the tool.
    """
    out: dict[str, Any] = {
        "added": 0,
        "reinforced": 0,
        "skipped": 0,
        "tool_saved": False,
    }
    text = (message or "").strip()
    if not text:
        return out
    mem = getattr(runtime, "memory", None)
    if mem is None:
        return out
    with suppress(Exception):
        from remedy.memory.partner_memory import (
            distill_user_text_sync,
            extract_heuristic_facts,
            is_explicit_remember_intent,
        )

        # Always run for explicit remember; light pass otherwise is handled
        # by speculative prep. Force sync path when user asked to remember.
        if not is_explicit_remember_intent(text):
            return out
        project_path = (
            str(
                getattr(getattr(runtime, "config", None), "project_path", None)
                or getattr(runtime, "_project_path", None)
                or ""
            )
            or None
        )
        sid = str(session_id or getattr(runtime, "_session_id", None) or "")
        result = distill_user_text_sync(
            mem,
            text,
            brief=getattr(runtime, "_session_brief", None),
            session_id=sid or None,
            project_path=project_path,
        )
        if isinstance(result, dict):
            out.update(result)

        # Belt-and-suspenders: same stores as memory_save tool (FTS note + profile)
        facts = extract_heuristic_facts(text)
        fact_texts = [f.text for f in facts if (f.text or "").strip()]
        if not fact_texts and isinstance(result, dict):
            fact_texts = list(result.get("facts") or [])
        if not fact_texts:
            import re

            m = re.search(
                r"(?i)remember(?:\s+(?:this|that|the|fact))?[:\s]+(.+?)(?:[.!?\n]|$)",
                text,
            )
            if m:
                fact_texts = [m.group(1).strip()]
        for ft in fact_texts[:3]:
            with suppress(Exception):
                _force_memory_save_sync(mem, str(ft), session_id=sid or None)
                out["tool_saved"] = True
    return out


async def _force_memory_save_async(
    memory: Any, content: str, *, session_id: str | None = None
) -> None:
    """Await the memory_save mirror directly — no private loop, no blocking.

    Runs on the caller's event loop so the shared, loop-bound MemoryStore is
    never driven from a second loop (and the uvicorn loop is never frozen).
    """
    import re

    from remedy.memory.partner_memory import looks_like_secret, upsert_profile_fact
    from remedy.models import MemoryEntry, MemoryEntryType

    text = re.sub(r"\s+", " ", (content or "").strip())
    if not text or looks_like_secret(text):
        return
    from remedy.memory.authority import may_write_parent_memory, stamp_entry_metadata

    meta = stamp_entry_metadata(
        {},
        source="explicit",
        session_id=session_id,
        inferred=False,
        why="remembered from chat",
    )
    await memory.upsert(
        MemoryEntry(
            title=text[:120] or "Remembered",
            content=text,
            entry_type=MemoryEntryType.NOTE,
            importance=0.85,
            session_id=session_id,
            metadata=meta,
        )
    )
    if may_write_parent_memory(session_id) and len(text) < 400:
        profile = await memory.get_or_create_profile()
        upsert_profile_fact(
            profile,
            text,
            category="general",
            confidence=0.95,
            source="explicit",
            force=True,
            inferred=False,
            authority="owner",
            why="remembered from chat",
            session_id=session_id,
        )
        await memory.save_user_profile(profile)


async def distill_user_message_now_async(
    runtime: Any,
    message: str,
    *,
    session_id: str | None = None,
    already_distilled: bool = False,
) -> dict[str, Any]:
    """Async twin of :func:`distill_user_message_now` — never blocks the loop.

    When *already_distilled* is True the main partner-memory distill was
    already awaited by the caller, so only the belt-and-suspenders
    memory_save mirror runs (awaited, not thread-blocked).
    """
    out: dict[str, Any] = {"added": 0, "reinforced": 0, "skipped": 0, "tool_saved": False}
    text = (message or "").strip()
    mem = getattr(runtime, "memory", None)
    if not text or mem is None:
        return out
    with suppress(Exception):
        from remedy.memory.partner_memory import (
            distill_user_text,
            extract_heuristic_facts,
            is_explicit_remember_intent,
        )

        if not is_explicit_remember_intent(text):
            return out
        project_path = (
            str(
                getattr(getattr(runtime, "config", None), "project_path", None)
                or getattr(runtime, "_project_path", None)
                or ""
            )
            or None
        )
        sid = str(session_id or getattr(runtime, "_session_id", None) or "")
        result: dict[str, Any] | None = None
        if not already_distilled:
            result = await distill_user_text(
                mem,
                text,
                brief=getattr(runtime, "_session_brief", None),
                session_id=sid or None,
                project_path=project_path,
            )
            if isinstance(result, dict):
                out.update(result)

        facts = extract_heuristic_facts(text)
        fact_texts = [f.text for f in facts if (f.text or "").strip()]
        if not fact_texts and isinstance(result, dict):
            fact_texts = list(result.get("facts") or [])
        if not fact_texts:
            import re

            m = re.search(
                r"(?i)remember(?:\s+(?:this|that|the|fact))?[:\s]+(.+?)(?:[.!?\n]|$)",
                text,
            )
            if m:
                fact_texts = [m.group(1).strip()]
        for ft in fact_texts[:3]:
            with suppress(Exception):
                await _force_memory_save_async(mem, str(ft), session_id=sid or None)
                out["tool_saved"] = True
    return out


def _force_memory_save_sync(
    memory: Any, content: str, *, session_id: str | None = None
) -> None:
    """Mirror memory_save tool: NOTE entry + partner profile fact (sync path)."""
    import asyncio
    import concurrent.futures
    import re

    from remedy.memory.partner_memory import looks_like_secret, upsert_profile_fact
    from remedy.models import MemoryEntry, MemoryEntryType

    text = re.sub(r"\s+", " ", (content or "").strip())
    if not text or looks_like_secret(text):
        return

    async def _run() -> None:
        from remedy.memory.authority import may_write_parent_memory, stamp_entry_metadata

        meta = stamp_entry_metadata(
            {},
            source="explicit",
            session_id=session_id,
            inferred=False,
            why="remembered from chat",
        )
        await memory.upsert(
            MemoryEntry(
                title=text[:120] or "Remembered",
                content=text,
                entry_type=MemoryEntryType.NOTE,
                importance=0.85,
                session_id=session_id,
                metadata=meta,
            )
        )
        if may_write_parent_memory(session_id) and len(text) < 400:
            profile = await memory.get_or_create_profile()
            upsert_profile_fact(
                profile,
                text,
                category="general",
                confidence=0.95,
                source="explicit",
                force=True,
                inferred=False,
                authority="owner",
                why="remembered from chat",
                session_id=session_id,
            )
            await memory.save_user_profile(profile)

    # Always use a private loop on a worker thread so this is safe from
    # async ReAct (running loop) and from sync callers.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(_run())).result(timeout=10)


def schedule_mid_turn_warm(
    runtime: Any,
    *,
    message: str = "",
    session_id: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> None:
    """Speculative prep during a long tool loop (same as post-turn, optional msgs)."""
    with suppress(Exception):
        from remedy.core.speculative import schedule_speculative_prep

        sid = str(session_id or getattr(runtime, "_session_id", None) or "")
        project_path = (
            str(
                getattr(getattr(runtime, "config", None), "project_path", None)
                or getattr(runtime, "_project_path", None)
                or ""
            )
            or None
        )
        schedule_speculative_prep(
            session_id=sid,
            brief=getattr(runtime, "_session_brief", None),
            messages=messages or getattr(runtime, "_last_send_messages", None),
            user_text=message or "",
            project_path=project_path,
            memory=getattr(runtime, "memory", None),
        )
