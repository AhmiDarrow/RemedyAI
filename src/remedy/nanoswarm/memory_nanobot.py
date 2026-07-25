"""Memory nanobot — continuous Session Brief freshness + fill signals."""

from __future__ import annotations

import re
from typing import Any

_DECISION_RE = re.compile(
    r"(?:decided to|deciding to|will use|choosing|strategy:)\s+([^.!?\n]{3,120})",
    re.I,
)
_NEXT_RE = re.compile(
    r"(?:next(?: step)?s?:|todo:|then we)\s+([^.!?\n]{3,120})",
    re.I,
)


class MemoryNanobot:
    """Incremental harness helper; uses TokenNanobot for fill %."""

    def __init__(self) -> None:
        self.last_fill_pct: float = 0.0
        self.last_nudge: str | None = None
        self.updates: int = 0

    def on_message(
        self,
        content: str,
        *,
        brief: Any | None = None,
        messages: list[dict[str, Any]] | None = None,
        context_window: int = 200_000,
        min_pct: float = 0.75,
        max_pct: float = 0.92,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        from remedy.nanoswarm.token_nanobot import get_token_nanobot

        signals: dict[str, Any] = {"bot": "memory"}
        if brief is not None and content:
            try:
                from remedy.memory.harness.compressor import extract_paths_from_text

                for p in extract_paths_from_text(content):
                    brief.add_artifact(p)
                for m in _DECISION_RE.finditer(content):
                    d = m.group(1).strip()
                    if d and d not in (brief.decisions or []):
                        brief.decisions = list(brief.decisions or []) + [d]
                        if len(brief.decisions) > 12:
                            brief.decisions = brief.decisions[-12:]
                for m in _NEXT_RE.finditer(content):
                    n = m.group(1).strip()
                    if n and n not in (brief.next_steps or []):
                        brief.next_steps = list(brief.next_steps or []) + [n]
                        if len(brief.next_steps) > 12:
                            brief.next_steps = brief.next_steps[-12:]
                self.updates += 1
                signals["brief_touched"] = True
            except Exception as e:
                signals["brief_error"] = str(e)

        token = get_token_nanobot()
        est = token.measure_messages(messages or [], provider=provider, model=model)
        fill = token.fill_pct(est, context_window=context_window)
        nudge = token.should_nudge_compress(
            est,
            context_window=context_window,
            min_pct=min_pct,
            max_pct=max_pct,
        )
        self.last_fill_pct = fill
        self.last_nudge = nudge
        signals["token_estimate"] = est
        signals["fill_pct"] = round(fill, 4)
        signals["nudge"] = nudge
        signals["estimate_method"] = token.last_method

        if brief is not None and nudge and messages:
            try:
                from remedy.memory.harness.compressor import heuristic_merge_from_history

                heuristic_merge_from_history(brief, messages)
                signals["proactive_merge"] = True
            except Exception:
                pass
        return signals

    def status(self) -> dict[str, Any]:
        return {
            "bot": "memory",
            "updates": self.updates,
            "last_fill_pct": self.last_fill_pct,
            "last_nudge": self.last_nudge,
        }
