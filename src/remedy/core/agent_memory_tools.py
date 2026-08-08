"""Memory harness + partner checkpoints tool registration (extracted from BasicRuntime)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def register_memory_tools(runtime: Any) -> None:
    """Memory + Memory Harness tools (search, save, compress)."""

    async def memory_search(query: str = "", limit: int = 8) -> str:
        if runtime.memory is None:
            return "Memory store not available."
        q = (query or "").strip()
        if not q:
            return "Provide a search query."
        try:
            from remedy.memory.partner_memory import search_partner_and_entries

            project_path = str(
                getattr(runtime, "_project_path", None)
                or getattr(getattr(runtime, "config", None), "project_path", None)
                or ""
            ) or None
            merged = await search_partner_and_entries(
                runtime.memory,
                q,
                limit=max(1, min(int(limit), 20)),
                project_path=project_path,
            )
        except Exception as e:
            return f"Memory search failed: {e}"
        if not merged:
            return f"No memory matches for: {q}"
        lines = []
        for hit in merged:
            kind = hit.get("kind") or "entry"
            title = hit.get("title") or kind
            content = (hit.get("content") or "")[:200]
            lines.append(f"- [{kind}] {title}: {content}")
        return "Memory hits:\n" + "\n".join(lines)

    async def memory_save(
        content: str = "",
        title: str = "Remembered",
        category: str = "general",
    ) -> str:
        if runtime.memory is None:
            return "Memory store not available."
        text = (content or "").strip()
        if not text:
            return "Nothing to save — provide content."
        try:
            from remedy.memory.partner_memory import looks_like_secret, upsert_profile_fact
            from remedy.models import MemoryEntry, MemoryEntryType

            if looks_like_secret(text):
                return (
                    "Refused: content looks like a secret (API key/password). "
                    "Do not store credentials in Partner Memory."
                )

            await runtime.memory.upsert(
                MemoryEntry(
                    title=(title or "Remembered")[:120],
                    content=text,
                    entry_type=MemoryEntryType.NOTE,
                    importance=0.75,
                )
            )
            # Also surface in the machine-native middleman (query-retrievable).
            with suppress(Exception):
                from remedy.memory.middleman import get_session_middleman

                get_session_middleman(
                    str(getattr(runtime, "_session_id", None) or "")
                ).put(
                    text,
                    kind="fact",
                    session_id=str(getattr(runtime, "_session_id", None) or ""),
                    body_cap=1_000,
                )
            # Also surface as a user fact when short
            if len(text) < 400:
                with suppress(Exception):
                    profile = await runtime.memory.get_or_create_profile()
                    upsert_profile_fact(
                        profile,
                        text,
                        category=category or "general",
                        confidence=0.9,
                        source="explicit",
                        force=True,
                    )
                    await runtime.memory.save_user_profile(profile)
            return f"Saved to memory: {(title or 'Remembered')[:80]}"
        except Exception as e:
            return f"Memory save failed: {e}"

    async def compress_context(focus: str = "") -> str:
        """Memory Harness L1: merge history into Session Brief (send-view stays lean)."""
        from remedy.core.session_quality import get_session_quality
        from remedy.memory.harness.brief import SessionBrief
        from remedy.memory.harness.compressor import (
            estimate_tokens,
            heuristic_merge_from_history,
        )
        from remedy.memory.harness.quality import review_compress_quality

        if runtime._session_brief is None:
            runtime._session_brief = SessionBrief(
                session_id=getattr(runtime, "_session_id", None) or ""
            )
        history: list[dict[str, Any]] = []
        sid = getattr(runtime, "_session_id", None)
        if sid and runtime.memory is not None:
            with suppress(Exception):
                history = await runtime._load_session_history(sid, "")
        tokens_before = 0
        with suppress(Exception):
            tokens_before = estimate_tokens(history)
        runtime._session_brief = heuristic_merge_from_history(
            runtime._session_brief,
            history,
            intent_hint=(focus or None),
        )
        brief = runtime._session_brief
        # Cumulative history thread entry (manual /compact)
        with suppress(Exception):
            brief.append_history_thread(
                "Manual compress_context"
                + (f" focus={focus[:120]}" if focus else "")
                + f". Intent: {(brief.intent or focus or '')[:200]}",
                decisions_why=list(brief.decisions[-4:]),
                blockers=list(brief.blockers[-3:]),
            )
        # Prefer free local model brief enrichment (non-blocking for next turn)
        with suppress(Exception):
            from remedy.memory.harness.local_brief import (
                schedule_background_brief_update,
            )

            schedule_background_brief_update(
                runtime,
                history,
                intent_hint=focus or "",
                level="strong",
            )
        tokens_after = 0
        with suppress(Exception):
            from remedy.memory.harness.brief import brief_to_context_block

            # Post-compress send view ≈ brief + recent tail estimate
            tokens_after = max(
                1,
                estimate_tokens(
                    [{"role": "system", "content": brief_to_context_block(brief) or ""}]
                )
                + min(tokens_before // 4, 4000),
            )
        quality: dict = {}
        with suppress(Exception):
            quality = review_compress_quality(
                messages_before=history,
                brief=brief,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
            )
            score = quality.get("score")
            if score is not None:
                brief.last_quality_score = float(score)
            get_session_quality(str(sid or "")).record_compress(
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                quality=quality,
                source="compress_context",
            )
        qline = ""
        if quality:
            qline = (
                f" Continuity check: {quality.get('summary')} "
                f"(score {quality.get('score')})."
            )
            if quality.get("paths_lost_sample"):
                lost = ", ".join(str(p) for p in quality["paths_lost_sample"][:3])
                qline += f" Watch paths: {lost}."
        return (
            f"Context compressed (pass #{brief.compress_count}). "
            f"Intent: {brief.intent or '(set)'}. "
            f"Files: {len(brief.artifacts)}. "
            f"Decisions: {len(brief.decisions)}. "
            f"History thread: {len(brief.history_thread)}."
            f"{qline}"
        )

    runtime.tool_registry.register_builtin_handler(
        "memory_search",
        "Search durable Remedy memory for relevant notes and facts.",
        memory_search,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "Max results (default 8)"},
            },
            "required": ["query"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "memory_save",
        "Save a durable note or fact about the user or project to memory.",
        memory_save,
        {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "title": {"type": "string"},
                "category": {"type": "string", "description": "e.g. work, personal, preference"},
            },
            "required": ["content"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "compress_context",
        "Memory Harness: compress stale session detail into the Session Brief "
        "(intent, files, decisions, next steps). Call when a subtask finishes or context is large.",
        compress_context,
        {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Optional focus for what to keep in the brief",
                },
            },
        },
    )

    async def memory_resolve(handle: str = "") -> str:
        """Resolve a working-memory handle to its full body (lazy middleman pull)."""
        h = (handle or "").strip()
        if not h:
            return "Provide a handle (remedy-mm://...) to resolve."
        sid = str(getattr(runtime, "_session_id", None) or "")
        try:
            from remedy.memory.middleman import get_session_middleman

            body = get_session_middleman(sid).get(h)
        except Exception as e:
            return f"memory_resolve failed: {e}"
        if body is None:
            return f"No working-memory item for handle: {h}"
        return body[:6000]

    runtime.tool_registry.register_builtin_handler(
        "memory_resolve",
        "Resolve a working-memory handle (remedy-mm://...) to its full body. "
        "Use when a retrieved memory block references a handle you need in full.",
        memory_resolve,
        {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "The remedy-mm:// handle from the working-memory block",
                },
            },
            "required": ["handle"],
        },
    )

    # --- Partner loop: goals + structured plans ---
    from remedy.core.agent_goals import register_goal_and_plan_tools

    register_goal_and_plan_tools(runtime)

    # --- Mid-task checkpoints (long Build runs) ---
    async def checkpoint_save(
        title: str = "",
        done: str = "",
        next_steps: str = "",
        reason: str = "manual",
    ) -> str:
        from uuid import uuid4

        from remedy.core.checkpoint import CheckpointStore, TurnCheckpoint

        home = getattr(runtime.config, "home_dir", None)
        store = CheckpointStore(home)
        done_list = [
            ln.strip("-*• ").strip()
            for ln in (done or "").splitlines()
            if ln.strip()
        ]
        next_list = [
            ln.strip("-*• ").strip()
            for ln in (next_steps or "").splitlines()
            if ln.strip()
        ]
        from remedy.core.turn_context import current_turn_tool_steps, turn_session_id

        steps = list(current_turn_tool_steps(runtime))
        tools = []
        for s in steps:
            t = str(s.get("tool") or "")
            if t and t not in tools:
                tools.append(t)
        cp = TurnCheckpoint(
            id=uuid4().hex[:12],
            session_id=str(turn_session_id(runtime) or "") or None,
            title=(title or "Manual checkpoint")[:200],
            done=done_list,
            next_steps=next_list or ["Continue the task"],
            tools_used=tools,
            tool_step_count=len(steps),
            reason=(reason or "manual")[:40],
        )
        store.save(cp)
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            if runtime._session_brief is None:
                runtime._session_brief = SessionBrief()
            for d in done_list[:5]:
                runtime._session_brief.decisions.append(f"Checkpoint: {d}")
            runtime._session_brief.decisions = runtime._session_brief.decisions[-20:]
            for n in next_list[:5]:
                if n not in runtime._session_brief.open_tasks:
                    runtime._session_brief.open_tasks.append(n)
            runtime._session_brief.open_tasks = runtime._session_brief.open_tasks[-20:]
            runtime._session_brief.touch()
        return cp.summary_markdown()

    async def checkpoint_show() -> str:
        from remedy.core.checkpoint import CheckpointStore

        store = CheckpointStore(getattr(runtime.config, "home_dir", None))
        sid = str(getattr(runtime, "_session_id", "") or "") or None
        cp = store.latest(sid)
        if cp is None:
            return "No checkpoints yet for this session."
        return cp.summary_markdown()

    runtime.tool_registry.register_builtin_handler(
        "checkpoint_save",
        "Save a mid-task checkpoint (done / next / blockers) so long work can resume safely.",
        checkpoint_save,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "done": {
                    "type": "string",
                    "description": "Bullet list of completed items",
                },
                "next_steps": {
                    "type": "string",
                    "description": "Bullet list of remaining work",
                },
                "reason": {"type": "string"},
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "checkpoint_show",
        "Show the latest mid-task checkpoint for this session.",
        checkpoint_show,
        {"type": "object", "properties": {}},
    )

    # --- Partner State Machine (subgoals / txns / graph / prospective) ---
    from remedy.core.agent_partner_tools import register_partner_state_tools

    register_partner_state_tools(runtime)

