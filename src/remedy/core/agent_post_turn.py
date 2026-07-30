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

        sid = str(
            session_id
            or getattr(runtime, "_session_id", None)
            or ""
        )
        qsnap = get_session_quality(sid).snapshot()
        project_path = str(
            getattr(getattr(runtime, "config", None), "project_path", None)
            or getattr(runtime, "_project_path", None)
            or ""
        ) or None
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
        project_path = str(
            getattr(getattr(runtime, "config", None), "project_path", None)
            or getattr(runtime, "_project_path", None)
            or ""
        ) or None
        sid = str(
            session_id
            or getattr(runtime, "_session_id", None)
            or ""
        )
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
                _force_memory_save_sync(mem, str(ft))
                out["tool_saved"] = True
    return out


def _force_memory_save_sync(memory: Any, content: str) -> None:
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
        await memory.upsert(
            MemoryEntry(
                title=text[:120] or "Remembered",
                content=text,
                entry_type=MemoryEntryType.NOTE,
                importance=0.85,
            )
        )
        if len(text) < 400:
            profile = await memory.get_or_create_profile()
            upsert_profile_fact(
                profile,
                text,
                category="general",
                confidence=0.95,
                source="explicit",
                force=True,
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

        sid = str(
            session_id
            or getattr(runtime, "_session_id", None)
            or ""
        )
        project_path = str(
            getattr(getattr(runtime, "config", None), "project_path", None)
            or getattr(runtime, "_project_path", None)
            or ""
        ) or None
        schedule_speculative_prep(
            session_id=sid,
            brief=getattr(runtime, "_session_brief", None),
            messages=messages or getattr(runtime, "_last_send_messages", None),
            user_text=message or "",
            project_path=project_path,
            memory=getattr(runtime, "memory", None),
        )
