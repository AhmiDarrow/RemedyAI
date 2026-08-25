"""Turn lifecycle events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class EventType(StrEnum):
    GOAL_STARTED = "GoalStarted"
    PLAN_CREATED = "PlanCreated"
    TOOL_PROPOSED = "ToolProposed"
    APPROVAL_REQUESTED = "ApprovalRequested"
    APPROVAL_GRANTED = "ApprovalGranted"
    TOOL_STARTED = "ToolStarted"
    TOOL_COMPLETED = "ToolCompleted"
    TOOL_FAILED = "ToolFailed"
    VERIFICATION_STARTED = "VerificationStarted"
    VERIFICATION_COMPLETED = "VerificationCompleted"
    EVIDENCE_RECORDED = "EvidenceRecorded"
    GOAL_COMPLETED = "GoalCompleted"
    GOAL_FAILED = "GoalFailed"
    HANDOFF_CREATED = "HandoffCreated"


@dataclass(frozen=True, slots=True)
class Event:
    event_type: EventType
    session_id: str
    turn_id: str
    actor: str = "remedy"
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
