"""L1 model-guided / threshold compress helpers for Memory Harness."""

from __future__ import annotations

import re
from typing import Any

from remedy.memory.harness.brief import SessionBrief


_PATH_RE = re.compile(
    r"(?:[A-Za-z]:)?[\\/][\w.\\/ -]{3,}|"
    r"[\w.-]+\.(?:py|ts|tsx|js|jsx|rs|go|md|toml|json|yml|yaml|css|html)\b"
)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate (~4 chars/token) for threshold checks."""
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif c is not None:
            total += len(str(c))
    return max(1, total // 4)


def should_nudge_compress(
    token_estimate: int,
    *,
    context_window: int = 128_000,
    min_pct: float = 0.35,
    max_pct: float = 0.70,
) -> str | None:
    """Return 'soft', 'strong', or None based on fill percentage."""
    if context_window <= 0:
        return None
    pct = token_estimate / context_window
    if pct >= max_pct:
        return "strong"
    if pct >= min_pct:
        return "soft"
    return None


def compression_nudge_message(level: str) -> dict[str, str]:
    if level == "strong":
        text = (
            "[Memory Harness] Context is high. Compress completed work now: "
            "call compress_context or summarize closed tool spans into the Session Brief "
            "(intent, decisions, files, next steps). Keep recent user messages intact."
        )
    else:
        text = (
            "[Memory Harness] Context is growing. When a subtask finishes, compress "
            "stale tool output into the Session Brief so later turns stay lean."
        )
    return {"role": "system", "content": text}


def extract_paths_from_text(text: str, *, limit: int = 20) -> list[str]:
    found: list[str] = []
    for m in _PATH_RE.finditer(text or ""):
        p = m.group(0).strip().rstrip(".,;:)")
        if p and p not in found:
            found.append(p)
        if len(found) >= limit:
            break
    return found


def heuristic_merge_from_history(
    brief: SessionBrief,
    messages: list[dict[str, Any]],
    *,
    intent_hint: str | None = None,
) -> SessionBrief:
    """Cheap L1 without an extra LLM call — extract paths + optional intent."""
    if intent_hint and intent_hint.strip() and not brief.intent:
        brief.intent = intent_hint.strip()[:500]
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        for p in extract_paths_from_text(content):
            brief.add_artifact(p)
        role = msg.get("role")
        if role == "user" and len(content) < 400 and not brief.intent:
            # First short user goal as intent fallback
            brief.intent = content.strip()[:500]
    brief.compress_count += 1
    brief.touch()
    return brief


def build_compress_summary_prompt(focus: str | None = None) -> str:
    focus_line = f"\nFocus: {focus}" if focus else ""
    return (
        "Produce a compact Session Brief update for Memory Harness as JSON only:\n"
        '{"intent":"...","decisions":["..."],"open_tasks":["..."],'
        '"next_steps":["..."],"blockers":["..."],"notes":"..."}\n'
        "Preserve file paths and technical specifics. Do not invent files."
        f"{focus_line}"
    )
