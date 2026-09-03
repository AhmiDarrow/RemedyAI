"""Bind durable missions to build-engine turns (goal + verify stickiness)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def _norm(path: str) -> str:
    return (path or "").strip().replace("\\", "/").rstrip("/").lower()


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
    project = ""
    with suppress(Exception):
        project = str(getattr(state, "project_path", "") or "") or str(
            runtime.effective_project_path() or ""
        )
    latest = store.latest(sid)
    vcmd = str(getattr(state, "verify_command", "") or "")
    goal = str(getattr(state, "goal", "") or "Complete build")[:300]

    # A mission from this session but another project (owner switched the
    # bound folder mid-session) is not this build's mission either.
    if (
        latest is not None
        and latest.project_path
        and project
        and _norm(latest.project_path) != _norm(project)
    ):
        latest = None

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
        "Implement the owner's goal (file_write / file_edit)",
        "Verify with project tests only after that work is on disk",
    ]
    m = create_mission(
        goal,
        steps=steps,
        session_id=sid,
        verify_command=vcmd or None,
        home=home,
        project_path=project or None,
    )
    if hasattr(state, "mission_id"):
        state.mission_id = m.id
    return {
        "ok": True,
        "mission_id": m.id,
        "created": True,
        "verify_command": vcmd,
    }


def _mission_step_is_verify(title: str) -> bool:
    """True for checklist rows that *are* the test run, not product work."""
    t = (title or "").lower()
    return any(
        k in t
        for k in (
            "verify",
            "pytest",
            "npm test",
            "run tests",
            "run the tests",
            "test suite",
            "tests green",
        )
    )


def note_mission_verify(runtime: Any, state: Any, *, ok: bool, output: str = "") -> None:
    """Update bound mission verify_status after auto-verify.

    A green suite is a checkpoint, not the whole mission. Session 765c marked
    "Implement the owner's goal" done on the first ``npm test`` pass.
    """
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
            for s in m.steps:
                if s.status in ("pending", "active") and _mission_step_is_verify(
                    s.title
                ):
                    s.status = "done"
            if m.steps and all(s.status in ("done", "skipped") for s in m.steps):
                m.status = "completed"
        else:
            m.retries = int(m.retries or 0) + 1
        store.save(m)
