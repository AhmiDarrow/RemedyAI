"""User profile modeling for Remedy companion personalization.

Tracks user traits, preferences, facts, and interaction patterns
to provide contextually-aware, personalized responses across sessions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class UserTrait(BaseModel):
    """A persistent trait or preference about the user."""

    key: str = Field(description="Trait identifier (e.g. 'preferred_language', 'timezone')")
    value: Any = Field(description="Current value")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="How confident we are")
    source: str = Field(default="inferred", description="How this trait was acquired")
    first_observed: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
    observation_count: int = Field(default=1)


class UserFact(BaseModel):
    """A single learned fact about the user."""

    id: UUID = Field(default_factory=uuid4)
    fact: str = Field(description="The fact itself")
    category: str = Field(default="general", description="e.g. 'work', 'personal', 'project'")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source: str = Field(default="inferred")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_referenced: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reference_count: int = Field(default=1)


class UserProfile(BaseModel):
    """Aggregated profile of the Remedy user (Reme companion context)."""

    user_id: str = Field(default="default")
    display_name: str | None = Field(default=None)
    traits: dict[str, UserTrait] = Field(default_factory=dict)
    facts: list[UserFact] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=lambda: {
        "sessions_count": 0,
        "total_interactions": 0,
        "skills_used": {},
        "preferred_channels": [],
        "avg_session_duration_minutes": 0.0,
    })
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_active: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def get_trait(self, key: str, default: Any = None) -> Any:
        t = self.traits.get(key)
        return t.value if t else default

    def set_trait(self, key: str, value: Any, confidence: float = 0.8, source: str = "explicit") -> None:
        if key in self.traits:
            t = self.traits[key]
            t.value = value
            t.confidence = max(t.confidence, confidence)
            t.last_updated = datetime.now(UTC)
            t.observation_count += 1
        else:
            self.traits[key] = UserTrait(
                key=key, value=value, confidence=confidence,
                source=source,
            )

    def add_fact(self, fact: str, category: str = "general", confidence: float = 0.7) -> UserFact:
        uf = UserFact(fact=fact, category=category, confidence=confidence)
        self.facts.append(uf)
        return uf

    def record_session(self, duration_minutes: float) -> None:
        self.stats["sessions_count"] += 1
        prev_avg = self.stats["avg_session_duration_minutes"]
        n = self.stats["sessions_count"]
        self.stats["avg_session_duration_minutes"] = (
            (prev_avg * (n - 1) + duration_minutes) / n
        )
        self.last_active = datetime.now(UTC)

    def record_skill_use(self, skill_name: str) -> None:
        self.stats["skills_used"][skill_name] = self.stats["skills_used"].get(skill_name, 0) + 1
