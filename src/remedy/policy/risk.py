"""Coarse risk class for a tool. Used by PolicyEngine, not by the LLM."""

from __future__ import annotations

from enum import StrEnum


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
