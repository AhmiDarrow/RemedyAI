"""Canonical tool invocation record (plan: tools/invocation.py)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from remedy.policy.decisions import ToolRequest


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def as_request(self, command: str = "") -> ToolRequest:
        return ToolRequest(name=self.name, arguments=dict(self.arguments), command=command)
