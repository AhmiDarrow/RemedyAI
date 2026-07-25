"""Pack nanobot — cheap context packing hints for the next provider turn.

Heuristics only (no network). Ranks recent tool pairs and brief pins so the
frontier model keeps high-signal slices under the active context window.
"""

from __future__ import annotations

from typing import Any


class PackNanobot:
    """Suggest which history slices / brief fields matter most next turn."""

    def __init__(self) -> None:
        self.last_pack: dict[str, Any] | None = None
        self.packs_run = 0

    def pack_for_turn(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        brief: Any | None = None,
        context_window: int = 128_000,
        fill_pct: float = 0.0,
        pattern_recent: list[str] | None = None,
        intent: str = "chat",
    ) -> dict[str, Any]:
        msgs = list(messages or [])
        keep_pairs = 4
        if fill_pct >= 0.85:
            keep_pairs = 2
        elif fill_pct >= 0.7:
            keep_pairs = 3
        elif intent in ("tool", "plan"):
            keep_pairs = 6

        # Count tool-ish messages at the end
        toolish = 0
        for m in reversed(msgs):
            role = str(m.get("role") or "")
            if role in ("tool", "function") or m.get("tool_calls"):
                toolish += 1
            else:
                break

        pins: list[str] = []
        if brief is not None:
            for attr in ("decisions", "next_steps", "artifacts"):
                vals = getattr(brief, attr, None) or []
                for v in list(vals)[:4]:
                    s = str(v).strip()
                    if s:
                        pins.append(s[:120])

        system_hint = ""
        if fill_pct >= 0.75 or toolish >= 6:
            recent = list(pattern_recent or [])[-keep_pairs:]
            system_hint = (
                "[Continuity/Pack] Prefer recent tool results and pinned decisions; "
                f"keep about {keep_pairs} recent tool pairs; drop older tool sludge."
            )
            if recent:
                system_hint += f" Recent tools: {', '.join(recent)}."
            if pins:
                system_hint += " Pins: " + "; ".join(pins[:5])

        out = {
            "bot": "pack",
            "keep_recent_tool_pairs": keep_pairs,
            "toolish_tail": toolish,
            "pins": pins[:8],
            "system_hint": system_hint,
            "context_window": context_window,
            "fill_pct": round(float(fill_pct or 0), 4),
            "aggressive": fill_pct >= 0.85,
        }
        self.last_pack = out
        self.packs_run += 1
        return out

    def status(self) -> dict[str, Any]:
        return {
            "bot": "pack",
            "packs_run": self.packs_run,
            "last_keep_pairs": (self.last_pack or {}).get("keep_recent_tool_pairs"),
            "last_aggressive": bool((self.last_pack or {}).get("aggressive")),
        }
