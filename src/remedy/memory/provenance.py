"""v0.36 durable-fact provenance. Web text is never USER_DECLARED."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class SourceType(StrEnum):
    USER_DECLARED = "USER_DECLARED"
    TOOL_OBSERVED = "TOOL_OBSERVED"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    MODEL_INFERRED = "MODEL_INFERRED"
    IMPORTED = "IMPORTED"


@dataclass
class FactRecord:
    text: str
    source_type: SourceType
    source_session: str = ""
    source_turn: str = ""
    source_event: str = ""
    confidence: float = 0.5
    user_confirmed: bool = False
    last_confirmed: datetime | None = None
    sensitivity: str = "normal"
    fact_id: str = field(default_factory=lambda: str(uuid4()))
    extra: dict[str, Any] = field(default_factory=dict)

    def from_web(self) -> bool:
        return str(self.extra.get("channel") or "") in ("web", "browser", "http")


def ingest_web_text(text: str, *, session_id: str = "", turn_id: str = "") -> FactRecord:
    """Pages can be TOOL_OBSERVED at low confidence — never USER_DECLARED."""
    return FactRecord(
        text=text,
        source_type=SourceType.TOOL_OBSERVED,
        source_session=session_id,
        source_turn=turn_id,
        confidence=0.2,
        user_confirmed=False,
        extra={"channel": "web"},
    )


def resolve_contradiction(old: FactRecord, new: FactRecord) -> FactRecord:
    """Lower confidence and keep the newer observation until the owner confirms."""
    if old.text.strip() == new.text.strip():
        return old
    return replace(
        new,
        confidence=min(old.confidence, new.confidence, 0.35),
        user_confirmed=False,
        last_confirmed=None,
        extra={
            **dict(new.extra),
            "contradicts": old.fact_id,
            "previous_text": old.text,
        },
    )
