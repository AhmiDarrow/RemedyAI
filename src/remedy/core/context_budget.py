"""v0.34 selective context budgets — do not only tail-truncate."""

from __future__ import annotations

from dataclasses import dataclass

_STORED_ARTIFACT = "\n…[stored artifact]…\n"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    history_chars: int = 120_000
    memory_chars: int = 24_000
    tool_result_chars: int = 4_000
    prompt_chars: int = 200_000


def clip_tool_result(text: str, budget: ContextBudget | None = None) -> str:
    """Keep the head and tail of a long tool result; store the middle."""
    cap = (budget or ContextBudget()).tool_result_chars
    if cap <= 0:
        return ""
    if len(text) <= cap:
        return text
    marker = _STORED_ARTIFACT
    if cap <= len(marker):
        return marker[:cap]
    remain = cap - len(marker)
    head = remain // 2
    tail = remain - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def clip_history(messages: list[str], budget: ContextBudget | None = None) -> list[str]:
    """Keep first user goal + newest messages within history_chars."""
    cap = (budget or ContextBudget()).history_chars
    if not messages:
        return []
    snapshot = list(messages)
    total = sum(len(m) for m in snapshot)
    if total <= cap:
        return snapshot
    keep: list[str] = [snapshot[0]]
    used = len(snapshot[0])
    suffix: list[str] = []
    for msg in reversed(snapshot[1:]):
        if used + len(msg) > cap:
            break
        suffix.append(msg)
        used += len(msg)
    keep.extend(reversed(suffix))
    return keep
