"""v0.35 skill contracts. Learning may propose; it cannot change policy."""

from __future__ import annotations

from dataclasses import dataclass

from remedy.policy.capabilities import Capability
from remedy.policy.risk import Risk


@dataclass(frozen=True, slots=True)
class SkillContract:
    name: str
    capabilities: frozenset[Capability]
    preconditions: tuple[str, ...] = ()
    verification: str = ""
    failure_modes: tuple[str, ...] = ()
    expected_cost: str = ""
    historical_success: float | None = None
    risk: Risk = Risk.MEDIUM


def learning_cannot_mutate_policy(proposed: object, current: SkillContract) -> SkillContract:
    """Ignore capability/risk edits from learned preferences."""
    _ = proposed
    return current
