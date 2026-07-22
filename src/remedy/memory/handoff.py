"""Auto-handoff manager -- generates handoff notes at session boundaries.

Critical for Remedy/Reme companion continuity. Automatically creates
structured handoffs when sessions end, capturing context, open items,
decisions, and suggesting follow-up tasks.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Optional

from remedy.memory.store import MemoryStore
from remedy.models import (
    HandoffNote,
    MemoryEntryType,
    SessionSummary,
    Task,
    TaskStatus,
)


class AutoHandoffManager:
    """Manages automatic handoff generation between sessions.

    Usage:
        mgr = AutoHandoffManager(store)

        # Start of session - load pending handoffs
        pending = await mgr.get_pending_handoffs()
        for h in pending:
            print(f"Continuing from: {h.title}")

        # End of session - generate handoff
        handoff = await mgr.generate_handoff(
            session_id="sess-001",
            tasks=[...completed tasks...],
            open_tasks=[...unfinished tasks...],
        )
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def get_pending_handoffs(self) -> list[HandoffNote]:
        """Get all unacknowledged handoff notes for session continuity."""
        all_handoffs = await self.store.list_handoffs(limit=50)
        return [h for h in all_handoffs if not h.acknowledged]

    async def acknowledge_all(self) -> int:
        """Acknowledge all pending handoffs. Returns count acknowledged."""
        pending = await self.get_pending_handoffs()
        count = 0
        for h in pending:
            if await self.store.ack_handoff(h.id):
                count += 1
        return count

    async def generate_handoff(
        self,
        session_id: str,
        tasks: Optional[list[Task]] = None,
        open_tasks: Optional[list[Task]] = None,
        extra_context: Optional[str] = None,
    ) -> HandoffNote:
        """Create a comprehensive handoff note for session transition.

        Aggregates: completed tasks, open items, session memory highlights,
        and any user-provided context into a structured handoff.
        """
        tasks = tasks or []
        open_tasks = open_tasks or []

        completed_summaries = [
            f"- {t.title}: {t.result_summary or 'completed'}"
            for t in tasks if t.status == TaskStatus.COMPLETED
        ]
        open_summaries = [
            f"- {t.title} [{t.status.value}]"
            for t in open_tasks
        ]

        recent_important = await self.store.list_important(threshold=0.7, limit=10)
        memory_highlights = [
            f"- {e.title}" for e in recent_important if e.session_id == session_id
        ]

        context_parts = []
        if extra_context:
            context_parts.append(extra_context)

        content_parts = []
        if completed_summaries:
            content_parts.append("**Completed this session**:\n" + "\n".join(completed_summaries))
        if open_summaries:
            content_parts.append("**Still open**:\n" + "\n".join(open_summaries))
        if memory_highlights:
            content_parts.append("**Key memories**:\n" + "\n".join(memory_highlights[:5]))

        content = "\n\n".join(content_parts) if content_parts else "Session ended."

        action_items = [t.title for t in open_tasks] if open_tasks else []

        return await self.store.create_handoff(HandoffNote(
            title=f"Handoff from session {session_id}",
            content=content,
            from_session=session_id,
            context_summary="\n".join(context_parts) if context_parts else None,
            action_items=action_items,
            decisions=[],
            tags=["auto-handoff", f"session-{session_id}"],
        ))

    async def generate_session_summary(
        self,
        session_id: str,
        started_at: datetime,
        tasks_completed: int = 0,
        skills_created: int = 0,
        skills_refined: int = 0,
        key_decisions: Optional[list[str]] = None,
        open_items: Optional[list[str]] = None,
    ) -> SessionSummary:
        """Create and persist a session summary."""
        summary = SessionSummary(
            session_id=session_id,
            started_at=started_at,
            tasks_completed=tasks_completed,
            skills_created=skills_created,
            skills_refined=skills_refined,
            key_decisions=key_decisions or [],
            open_items=open_items or [],
            summary=f"Session {session_id}: {tasks_completed} tasks, "
                    f"{skills_created} skills created.",
        )
        return await self.store.save_session_summary(summary)
