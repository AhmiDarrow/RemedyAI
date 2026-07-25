"""Nano swarm coordinator — event fan-out + public status."""

from __future__ import annotations

import threading
import time
from typing import Any  # noqa: F401 — used by job handlers

from remedy.nanoswarm.events import SwarmEvent
from remedy.nanoswarm.helper_nanobot import HelperNanobot
from remedy.nanoswarm.memory_nanobot import MemoryNanobot
from remedy.nanoswarm.pattern_nanobot import PatternNanobot
from remedy.nanoswarm.router_nanobot import RouterNanobot
from remedy.nanoswarm.skill_nanobot import SkillNanobot
from remedy.nanoswarm.token_nanobot import get_token_nanobot


class NanoSwarm:
    """Remedy's nano swarm brain — modular bots, one shared local Qwen for neural assist."""

    def __init__(self) -> None:
        self.token = get_token_nanobot()
        self.pattern = PatternNanobot()
        self.memory = MemoryNanobot()
        self.skill = SkillNanobot()
        self.router = RouterNanobot()
        self.helper = HelperNanobot()
        self._lock = threading.Lock()
        self._event_count = 0
        self._last_event: str | None = None
        self._last_ts: float = 0.0
        self.started_at = time.time()

    def dispatch(self, event: SwarmEvent, **ctx: Any) -> dict[str, Any]:
        """Route event to bots; ctx may include brief, messages, learning_loop, etc."""
        with self._lock:
            self._event_count += 1
            self._last_event = event.name
            self._last_ts = time.time()
        results: dict[str, Any] = {"event": event.name, "signals": {}}

        if event.name == "message_added":
            content = str(event.payload.get("content") or "")
            results["signals"]["memory"] = self.memory.on_message(
                content,
                brief=ctx.get("brief"),
                messages=ctx.get("messages"),
                context_window=int(ctx.get("context_window") or 200_000),
                min_pct=float(ctx.get("min_pct") or 0.75),
                max_pct=float(ctx.get("max_pct") or 0.92),
                provider=ctx.get("provider"),
                model=ctx.get("model"),
            )
            if event.payload.get("role") == "user":
                # Hot path: heuristics only (local refine is opt-in / API — never
                # block agent turns waiting on llama-server).
                results["signals"]["router"] = self.router.classify_intent(content)

        elif event.name == "tool_step":
            results["signals"]["pattern"] = self.pattern.on_tool_step(
                str(event.payload.get("tool_name") or "unknown"),
                success=bool(event.payload.get("success", True)),
                duration_ms=float(event.payload.get("duration_ms") or 0),
            )

        elif event.name == "skill_result":
            results["signals"]["skill"] = self.skill.on_skill_result(
                str(event.payload.get("skill_name") or ""),
                success=bool(event.payload.get("success", True)),
                learning_loop=ctx.get("learning_loop"),
                duration_ms=float(event.payload.get("duration_ms") or 0),
                skill=ctx.get("skill"),
            )

        elif event.name == "session_end":
            results["signals"]["pattern_pregate"] = self.pattern.pregate_trace(
                overall_success=bool(event.payload.get("success", True)),
                title=str(event.payload.get("title") or ""),
            )

        elif event.name == "provider_changed":
            # Token calibrator keeps per-provider buckets; nothing else required
            results["signals"]["token"] = {
                "provider": event.payload.get("provider"),
                "model": event.payload.get("model"),
            }

        return results

    def status(self) -> dict[str, Any]:
        from remedy.runtime.catalog import DEFAULT_LOCAL_MODEL_ID, LOCAL_ROLES

        return {
            "name": "Remedy Nano Swarm",
            "active": True,
            "event_count": self._event_count,
            "last_event": self._last_event,
            "last_event_ts": self._last_ts,
            "uptime_s": round(time.time() - self.started_at, 1),
            "local_model_id": DEFAULT_LOCAL_MODEL_ID,
            "roles": list(LOCAL_ROLES),
            "bots": {
                "token": self.token.status(),
                "pattern": self.pattern.status(),
                "memory": self.memory.status(),
                "skill": self.skill.status(),
                "router": self.router.status(),
                "helper": self.helper.status(),
            },
        }


_swarm: NanoSwarm | None = None
_swarm_lock = threading.Lock()


def get_swarm() -> NanoSwarm:
    global _swarm
    with _swarm_lock:
        if _swarm is None:
            _swarm = NanoSwarm()
        return _swarm
