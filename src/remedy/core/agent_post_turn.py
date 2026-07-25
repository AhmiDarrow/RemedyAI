"""Post-turn continuity hooks extracted from BasicRuntime.

Keeps project learning + speculative prep off the ReAct stream body so the
orchestrator only schedules work after a turn completes (or mid-tool warm).
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def schedule_post_turn_prep(
    runtime: Any,
    *,
    message: str = "",
    session_id: str | None = None,
) -> None:
    """Warm brief/memory/skills and lightly record project profile.

    Safe to call from stream finally-paths; never raises to the caller.
    """
    with suppress(Exception):
        from remedy.core.project_learning import record_session_end
        from remedy.core.session_quality import get_session_quality
        from remedy.core.speculative import schedule_speculative_prep

        sid = str(
            session_id
            or getattr(runtime, "_session_id", None)
            or ""
        )
        qsnap = get_session_quality(sid).snapshot()
        project_path = str(
            getattr(getattr(runtime, "config", None), "project_path", None)
            or getattr(runtime, "_project_path", None)
            or ""
        ) or None
        # Light touch each turn (not only true session end)
        if project_path and int(qsnap.get("turns") or 0) > 0:
            # Only merge full profile every few turns to limit disk IO
            if int(qsnap.get("turns") or 0) % 5 == 0:
                record_session_end(project_path, qsnap)
        schedule_speculative_prep(
            session_id=sid,
            brief=getattr(runtime, "_session_brief", None),
            messages=getattr(runtime, "_last_send_messages", None),
            user_text=message or "",
            project_path=project_path,
            memory=getattr(runtime, "memory", None),
        )


def schedule_mid_turn_warm(
    runtime: Any,
    *,
    message: str = "",
    session_id: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> None:
    """Speculative prep during a long tool loop (same as post-turn, optional msgs)."""
    with suppress(Exception):
        from remedy.core.speculative import schedule_speculative_prep

        sid = str(
            session_id
            or getattr(runtime, "_session_id", None)
            or ""
        )
        project_path = str(
            getattr(getattr(runtime, "config", None), "project_path", None)
            or getattr(runtime, "_project_path", None)
            or ""
        ) or None
        schedule_speculative_prep(
            session_id=sid,
            brief=getattr(runtime, "_session_brief", None),
            messages=messages or getattr(runtime, "_last_send_messages", None),
            user_text=message or "",
            project_path=project_path,
            memory=getattr(runtime, "memory", None),
        )
