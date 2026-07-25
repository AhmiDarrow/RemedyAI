"""Nano swarm event types (in-process signals)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SwarmEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def message_added(role: str, content: str, **extra: Any) -> SwarmEvent:
        return SwarmEvent(
            "message_added",
            {"role": role, "content": content, **extra},
        )

    @staticmethod
    def tool_step(
        tool_name: str,
        *,
        success: bool = True,
        duration_ms: float = 0.0,
        **extra: Any,
    ) -> SwarmEvent:
        return SwarmEvent(
            "tool_step",
            {
                "tool_name": tool_name,
                "success": success,
                "duration_ms": duration_ms,
                **extra,
            },
        )

    @staticmethod
    def skill_result(skill_name: str, *, success: bool, **extra: Any) -> SwarmEvent:
        return SwarmEvent(
            "skill_result",
            {"skill_name": skill_name, "success": success, **extra},
        )

    @staticmethod
    def session_end(session_id: str | None = None, **extra: Any) -> SwarmEvent:
        return SwarmEvent("session_end", {"session_id": session_id, **extra})

    @staticmethod
    def provider_changed(provider: str, model: str | None = None, **extra: Any) -> SwarmEvent:
        return SwarmEvent(
            "provider_changed",
            {"provider": provider, "model": model, **extra},
        )

    @staticmethod
    def usage_observed(
        *,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        source: str = "provider",
        session_id: str | None = None,
        **extra: Any,
    ) -> SwarmEvent:
        return SwarmEvent(
            "usage_observed",
            {
                "provider": provider,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": estimated_cost_usd,
                "source": source,
                "session_id": session_id,
                **extra,
            },
        )
