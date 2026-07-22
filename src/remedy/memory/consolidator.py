"""Memory consolidation -- periodic summarization, deduplication, and
importance boosting for long-term memory health.

Inspired by Hermes' memory reflection and compaction patterns.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Optional

from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType


class MemoryConsolidator:
    """Periodically consolidates raw memory entries into higher-level
    summaries, boosts frequently-referenced entries, and prunes noise."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def consolidate_session(
        self, session_id: str, max_entries: int = 100
    ) -> Optional[MemoryEntry]:
        """Summarize a session's entries into a single condensed note.

        Returns the consolidation entry, or None if nothing to consolidate.
        """
        entries = await self.store.list_recent(limit=max_entries)
        session_entries = [
            e for e in entries
            if e.session_id == session_id and e.entry_type != MemoryEntryType.SYSTEM
        ]
        if len(session_entries) < 3:
            return None

        topics = self._extract_topics(session_entries)
        highlights = self._pick_highlights(session_entries)
        action_items = self._extract_action_items(session_entries)

        summary = (
            f"Session {session_id} consolidated summary.\n\n"
            f"**Topics**: {', '.join(topics[:5])}\n\n"
            f"**Highlights**:\n" + "\n".join(f"- {h}" for h in highlights[:5]) + "\n\n"
            f"**Action items**:\n" + "\n".join(f"- {a}" for a in action_items[:5])
            if action_items else ""
        )

        consolidated = MemoryEntry(
            entry_type=MemoryEntryType.SESSION,
            title=f"Consolidation: {session_id}",
            content=summary,
            tags=list(topics[:5]),
            session_id=session_id,
            importance=0.75,
            metadata={
                "consolidated_from": [str(e.id) for e in session_entries],
                "entry_count": len(session_entries),
                "topics": topics[:5],
            },
        )
        return await self.store.upsert(consolidated)

    async def boost_importance(self, threshold: int = 3) -> int:
        """Boost importance on entries referenced multiple times."""
        db = self.store._ensure_db()
        rows = db.execute(
            "SELECT id, importance FROM memory_entries "
            "WHERE json_extract(metadata, '$.reference_count') >= ?",
            (threshold,),
        ).fetchall()
        count = 0
        for row in rows:
            new_imp = min(1.0, row["importance"] + 0.15)
            db.execute(
                "UPDATE memory_entries SET importance = ? WHERE id = ?",
                (new_imp, row["id"]),
            )
            count += 1
        db.commit()
        return count

    async def deduplicate(self, similarity_threshold: float = 0.8) -> int:
        """Find and merge near-duplicate entries based on title/content similarity."""
        entries = await self.store.list_recent(limit=200)
        removed = 0
        seen: dict[str, list[str]] = {}

        for entry in entries:
            key = entry.title.lower().strip()
            if key in seen:
                # Merge tags from duplicate into oldest entry
                oldest_id = seen[key][0]
                oldest = await self.store.get(oldest_id)
                if oldest is not None:
                    merged_tags = list(set(oldest.tags + entry.tags))
                    oldest.tags = merged_tags
                    oldest.metadata["merged_from"] = oldest.metadata.get("merged_from", []) + [
                        str(entry.id)
                    ]
                    await self.store.upsert(oldest)
                await self.store.delete(entry.id)
                removed += 1
            else:
                seen[key] = [str(entry.id)]

        return removed

    def _extract_topics(self, entries: list[MemoryEntry]) -> list[str]:
        tag_counter: Counter = Counter()
        for e in entries:
            for t in e.tags:
                tag_counter[t] += 1
        return [t for t, _ in tag_counter.most_common(5)]

    def _pick_highlights(self, entries: list[MemoryEntry]) -> list[str]:
        sorted_entries = sorted(entries, key=lambda e: e.importance, reverse=True)
        highlights = []
        for e in sorted_entries[:5]:
            preview = e.content[:100].replace("\n", " ")
            highlights.append(f"[{e.entry_type.value}] {e.title}: {preview}...")
        return highlights

    def _extract_action_items(self, entries: list[MemoryEntry]) -> list[str]:
        items = []
        for e in entries:
            ai = e.metadata.get("action_items", [])
            if isinstance(ai, list):
                items.extend(ai)
        return list(dict.fromkeys(items))[:10]
