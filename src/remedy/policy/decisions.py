"""PolicyEngine inputs and outputs. Frozen — no mid-turn mutation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from remedy.policy.capabilities import Capability


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """One proposed invocation (arguments as the model supplied them)."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    command: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", dict(self.arguments or {}))
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "command", str(self.command or ""))


@dataclass(frozen=True, slots=True)
class Constraint:
    kind: str
    value: str = ""


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    granted_capabilities: frozenset[Capability] = frozenset()
    constraints: tuple[Constraint, ...] = ()
