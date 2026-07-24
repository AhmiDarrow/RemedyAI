"""Post-turn auto-learn helpers extracted from BasicRuntime.

Keeps the personal-partner learning loop easy to unit-test without pulling
the full ReAct agent. Behavior matches the previous inline implementation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Meta tools alone do not count toward a "real work" multi-tool turn.
_META_TOOLS = frozenset({"skill_search", "skill_activate", "local_discover"})


def should_auto_learn_from_steps(steps: list[dict[str, Any]] | None) -> bool:
    """Return True when a turn has enough successful tool work to codify."""
    steps = list(steps or [])
    if len(steps) < 3:
        return False
    real = [s for s in steps if s.get("tool") not in _META_TOOLS]
    if len(real) < 3 and len(steps) < 4:
        if len(steps) < 4:
            return False
    successes = sum(1 for s in steps if s.get("success"))
    if successes < 3:
        return False
    return successes >= max(2, int(0.5 * len(steps)))


def auto_learn_from_turn(
    *,
    learning_loop: Any,
    message: str,
    session_id: str | None,
    steps: list[dict[str, Any]] | None,
) -> Any | None:
    """If eligible, distill tool steps into a probation skill. Returns skill or None."""
    if learning_loop is None:
        return None
    steps_list = list(steps or [])
    if not should_auto_learn_from_steps(steps_list):
        return None
    title = (message or "session-task").strip().split("\n")[0][:80]
    skill = learning_loop.learn_from_tool_steps(
        title=title or "multi-tool-task",
        steps=steps_list,
        session_id=session_id,
        description=(message or "")[:400],
        overall_success=True,
    )
    if skill is not None:
        logger.info(
            "Auto-learned skill '%s' status=%s",
            skill.manifest.name,
            skill.manifest.status.value,
        )
    return skill
