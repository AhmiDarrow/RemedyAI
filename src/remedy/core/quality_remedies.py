"""Quality metrics → automatic silent remedies (control loop, not a dashboard).

When stuck/re-explain rates rise, inject short system guidance so the next
model turn recovers continuity — still one mind (Remedy), no bot theater.
"""

from __future__ import annotations

from typing import Any


def remedies_from_quality(
    quality: dict[str, Any] | None,
    *,
    fill_pct: float = 0.0,
    nudge: str | None = None,
) -> dict[str, Any]:
    """Return {system, actions[]} based on session quality snapshot."""
    q = quality or {}
    actions: list[str] = []
    lines: list[str] = []

    re_rate = float(q.get("re_explain_rate") or 0)
    stuck_rate = float(q.get("stuck_rate") or 0)
    re_n = int(q.get("re_explain_count") or 0)
    stuck_n = int(q.get("stuck_signal_count") or 0)
    fail_streak = int(q.get("max_tool_fail_streak") or 0)
    last = q.get("last_compress") or {}
    q_score = last.get("quality_score")
    if q_score is None:
        q_score = q.get("avg_compress_quality")

    # Re-explain: user restating — trust brief / memory harder
    if re_n >= 1 and re_rate >= 0.15:
        actions.append("re_explain_anchor")
        lines.append(
            "[Continuity] User may be restating something already known. "
            "Re-read Session Brief and durable memory; do not re-ask settled facts; "
            "acknowledge and proceed with the corrected constraint."
        )

    # Stuck loop
    if stuck_n >= 2 or stuck_rate >= 0.2 or fail_streak >= 3:
        actions.append("stuck_recovery")
        lines.append(
            "[Continuity] Recent loop risk. Change approach: smaller tool args, "
            "list_dir/discover before guessing paths, skill_search for a known procedure, "
            "or propose a short plan before more shell. Do not repeat the same failed call."
        )

    # Weak last compress
    if q_score is not None and float(q_score) < 0.55:
        actions.append("weak_compress")
        lost = last.get("paths_lost") or 0
        lines.append(
            "[Continuity] Recent context compression may have dropped detail"
            + (f" (paths lost≈{lost})" if lost else "")
            + ". Prefer Session Brief artifacts; re-read key files if unsure; "
            "do not invent paths that are not in context."
        )

    # High fill without strong nudge yet
    if fill_pct >= 0.7 and nudge != "strong" and not actions:
        actions.append("fill_awareness")
        lines.append(
            "[Continuity] Context is getting large. Prefer concise tool results and "
            "compress completed subtasks into the Session Brief when a phase finishes."
        )

    system = ""
    if lines:
        system = "\n".join(lines)

    return {
        "actions": actions,
        "system": system,
        "triggered": bool(actions),
    }
