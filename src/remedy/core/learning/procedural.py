"""Procedural memory -- links learned skills to the persistent memory store.

Bridges the learning loop to the memory system:
- Stores generated skills as memory entries with context
- Links skills to the session and task that produced them
- Tracks learning events over time
- Provides recall of previously learned skills
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from remedy.models import (
    MemoryEntry,
    MemoryEntryType,
    Skill,
    SkillStatus,
)


@dataclass
class LearningEvent:
    """A record of a skill being learned or refined."""
    id: UUID = field(default_factory=uuid4)
    event_type: str = "created"  # "created", "refined", "promoted", "demoted", "replaced"
    skill_name: str = ""
    skill_version: str = ""
    source_trace_id: UUID | None = None
    source_session_id: str | None = None
    confidence_at_creation: float = 0.0
    description: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class LearningHistory:
    """Complete learning history for the agent."""
    events: list[LearningEvent] = field(default_factory=list)
    total_skills_created: int = 0
    total_skills_refined: int = 0
    skills_by_session: dict[str, list[str]] = field(default_factory=dict)

    def record_creation(
        self,
        skill: Skill,
        source_trace_id: UUID | None = None,
        source_session_id: str | None = None,
    ) -> LearningEvent:
        event = LearningEvent(
            event_type="created",
            skill_name=skill.manifest.name,
            skill_version=skill.manifest.version,
            source_trace_id=source_trace_id,
            source_session_id=source_session_id,
            confidence_at_creation=1.0,
            description=f"Created skill '{skill.manifest.name}'",
        )
        self.events.append(event)
        self.total_skills_created += 1

        if source_session_id:
            if source_session_id not in self.skills_by_session:
                self.skills_by_session[source_session_id] = []
            self.skills_by_session[source_session_id].append(skill.manifest.name)

        return event

    def record_refinement(
        self,
        skill_name: str,
        from_version: str,
        to_version: str,
        reason: str = "",
    ) -> LearningEvent:
        event = LearningEvent(
            event_type="refined",
            skill_name=skill_name,
            skill_version=to_version,
            description=f"Refined '{skill_name}' {from_version} -> {to_version}: {reason}",
        )
        self.events.append(event)
        self.total_skills_refined += 1
        return event

    def record_status_change(
        self,
        skill_name: str,
        from_status: SkillStatus,
        to_status: SkillStatus,
    ) -> LearningEvent:
        event = LearningEvent(
            event_type="promoted" if to_status == SkillStatus.ACTIVE else "demoted",
            skill_name=skill_name,
            description=f"{skill_name}: {from_status.value} -> {to_status.value}",
        )
        self.events.append(event)
        return event

    def get_skills_for_session(self, session_id: str) -> list[str]:
        return self.skills_by_session.get(session_id, [])

    def get_recent(self, limit: int = 20) -> list[LearningEvent]:
        return sorted(self.events, key=lambda e: e.occurred_at, reverse=True)[:limit]

    def to_memory_entries(self) -> list[MemoryEntry]:
        return [
            MemoryEntry(
                id=e.id,
                entry_type=MemoryEntryType.SKILL_LEARNED,
                title=f"Learning: {e.skill_name} ({e.event_type})",
                content=e.description,
                session_id=e.source_session_id,
                importance=0.7 if e.event_type == "created" else 0.5,
                metadata={
                    "skill_name": e.skill_name,
                    "skill_version": e.skill_version,
                    "event_type": e.event_type,
                },
                created_at=e.occurred_at,
            )
            for e in self.events
        ]
