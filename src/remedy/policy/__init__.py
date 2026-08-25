"""Capability-based policy: the LLM proposes; this package owns authority."""

from __future__ import annotations

from typing import Any

from remedy.policy.capabilities import Capability, CapabilitySet
from remedy.policy.decisions import Constraint, PolicyDecision, ToolRequest
from remedy.policy.risk import Risk

__all__ = [
    "Capability",
    "CapabilitySet",
    "Constraint",
    "PolicyDecision",
    "PolicyEngine",
    "Risk",
    "ToolRequest",
]


def __getattr__(name: str) -> Any:
    if name == "PolicyEngine":
        from remedy.policy.engine import PolicyEngine

        return PolicyEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
