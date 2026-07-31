"""Post-turn auto-learn helpers extracted from BasicRuntime.

Keeps the personal-partner learning loop easy to unit-test without pulling
the full ReAct agent. Behavior matches the previous inline implementation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Meta tools alone do not count toward a "real work" multi-tool turn.
_META_TOOLS = frozenset({"skill_search", "skill_activate", "local_discover"})


def _skill_title_from_steps(message: str, steps: list[dict[str, Any]]) -> str:
    """Build a short, stable skill title from tool names — not the full user path dump."""
    tools: list[str] = []
    for s in steps:
        name = str(s.get("tool") or s.get("name") or "").strip()
        if name and name not in _META_TOOLS and name not in tools:
            tools.append(name)
        if len(tools) >= 3:
            break
    if tools:
        return ("-".join(tools))[:60]
    # Fallback: first line of user message, strip drive paths.
    line = (message or "session-task").strip().split("\n")[0]
    line = re.sub(r"[A-Za-z]:\\[^\s]+", "", line)
    line = re.sub(r"/[^\s]+", "", line)
    line = re.sub(r"\s+", " ", line).strip(" -_.,")
    return (line or "session-task")[:60]


def should_auto_learn_from_steps(steps: list[dict[str, Any]] | None) -> bool:
    """Return True when a turn has enough successful tool work to codify.

    Requires multi-tool diversity so trivial file_read spam does not flood
    the skill catalog (coding long-task product bug).
    """
    steps = list(steps or [])
    if len(steps) < 3:
        return False
    real = [
        s
        for s in steps
        if (s.get("tool") or s.get("name") or s.get("tool_name")) not in _META_TOOLS
    ]
    if len(real) < 3 and len(steps) < 4:
        if len(steps) < 4:
            return False
    successes = sum(1 for s in steps if s.get("success"))
    if successes < 3:
        return False
    if successes < max(2, int(0.5 * len(steps))):
        return False
    # Distinct non-meta tools: pure explore loops (read/read/list) are noise.
    names: list[str] = []
    for s in real:
        n = str(s.get("tool") or s.get("name") or s.get("tool_name") or "").strip()
        if n and n not in names:
            names.append(n)
    if len(names) < 2:
        return False
    # Need at least 4 real steps OR 3 distinct tools — hard-won short paths OK via lifecycle
    return not (len(real) < 4 and len(names) < 3)


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

    # Pattern nanobot pre-gate: skip learning noisy / rejectable traces.
    # Prefer a short tool-pattern title over the raw user prompt (paths make
    # garbage skill ids like "file_read-i-like-our-assets-for-remedy-located-…").
    title = _skill_title_from_steps(message, steps_list)
    try:
        from remedy.nanoswarm import get_swarm
        from remedy.nanoswarm.events import SwarmEvent

        gate = get_swarm().dispatch(
            SwarmEvent.session_end(session_id, success=True, title=title or ""),
        )
        preg = (gate.get("signals") or {}).get("pattern_pregate") or {}
        if preg.get("skip_learn") or preg.get("action") in ("reject", "skip"):
            logger.info(
                "Auto-learn skipped by pattern pregate: %s",
                preg.get("reasoning") or preg.get("action"),
            )
            return None
    except Exception:
        logger.debug("pattern pregate failed", exc_info=True)

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
