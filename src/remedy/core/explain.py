"""v0.37 explainable autonomy from turn events.

``EventBus`` is owned by the events slice. ``explain_turn`` only needs
``for_turn(turn_id)`` plus objects with ``event_type`` and ``payload``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class TurnEventBus(Protocol):
    def for_turn(self, turn_id: str) -> Sequence[Any]: ...


def _event_name(event_type: Any) -> str:
    if event_type is None:
        return ""
    value = getattr(event_type, "value", event_type)
    return str(value)


def explain_turn(bus: TurnEventBus, turn_id: str) -> dict[str, str]:
    rows = bus.for_turn(turn_id)
    did: list[str] = []
    why: list[str] = []
    verified: list[str] = []
    remains: list[str] = []
    for ev in rows:
        name = _event_name(getattr(ev, "event_type", ""))
        payload = dict(getattr(ev, "payload", None) or {})
        if name in ("ToolCompleted", "ToolStarted"):
            did.append(str(payload.get("tool") or name))
        if name == "ApprovalRequested":
            why.append(str(payload.get("reason") or "approval"))
        if name == "VerificationCompleted":
            verified.append(str(payload.get("reason") or "verified"))
        if name == "GoalFailed":
            remains.append(str(payload.get("reason") or "failed"))
    return {
        "what": "; ".join(did) or "no tools ran",
        "why": "; ".join(why) or "policy allowed the turn",
        "verified": "; ".join(verified) or "no verifier ran",
        "remains": "; ".join(remains) or "nothing flagged",
    }
