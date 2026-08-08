"""Bind durable missions to build-engine turns (goal + verify stickiness)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def ensure_build_mission(
    runtime: Any,
    state: Any,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Create or refresh a mission for this build if none active.

    Returns {ok, mission_id, created, verify_command}.
    """
    from remedy.core.mission import MissionStore, create_mission

    home = getattr(getattr(runtime, "config", None), "home_dir", None)
    sid = session_id
    if not sid:
        with suppress(Exception):
            from remedy.core.turn_context import turn_session_id

            sid = turn_session_id(runtime)
    store = MissionStore(home)
    latest = store.latest(sid)
    vcmd = str(getattr(state, "verify_command", "") or "")
    goal = str(getattr(state, "goal", "") or "Complete build")[:300]

    if latest is not None and latest.status == "active":
        # Attach oracle command if mission lacks one
        if vcmd and not latest.verify_command:
            latest.verify_command = vcmd
            store.save(latest)
        if hasattr(state, "mission_id"):
            state.mission_id = latest.id
        return {
            "ok": True,
            "mission_id": latest.id,
            "created": False,
            "verify_command": latest.verify_command or vcmd,
        }

    steps = [
        "Scout codebase (batch reads)",
        "Implement changes (file_write / file_edit)",
        "Verify with project tests",
        "Repair until green",
    ]
    m = create_mission(
        goal,
        steps=steps,
        session_id=sid,
        verify_command=vcmd or None,
        home=home,
    )
    if hasattr(state, "mission_id"):
        state.mission_id = m.id
    return {
        "ok": True,
        "mission_id": m.id,
        "created": True,
        "verify_command": vcmd,
    }


def note_mission_verify(runtime: Any, state: Any, *, ok: bool, output: str = "") -> None:
    """Update bound mission verify_status after auto-verify."""
    mid = getattr(state, "mission_id", None)
    if not mid:
        return
    with suppress(Exception):
        from remedy.core.mission import MissionStore

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        store = MissionStore(home)
        m = store.get(str(mid))
        if m is None:
            return
        m.verify_status = "passed" if ok else "failed"
        m.last_verify_output = (output or "")[:4000]
        if ok:
            m.status = "completed"
            for s in m.steps:
                if s.status in ("pending", "active"):
                    s.status = "done"
        else:
            m.retries = int(m.retries or 0) + 1
        store.save(m)
