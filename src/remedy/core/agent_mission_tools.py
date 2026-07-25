"""Mission + silent job tools for work-alone agency."""

from __future__ import annotations

import json
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.core.jobs import run_job
from remedy.core.mission import (
    MissionStore,
    advance_step,
    create_mission,
    mission_summary,
)


def register_mission_tools(runtime: Any) -> None:
    """Register mission_* and job_run tools."""

    def _home() -> str | None:
        return getattr(getattr(runtime, "config", None), "home_dir", None)

    async def mission_start(
        goal: str = "",
        steps: str = "",
        verify_command: str = "",
    ) -> str:
        """Start a durable mission (checklist + optional verify command)."""
        g = (goal or "").strip()
        if not g:
            return format_tool_error(
                "goal is required",
                code="MISSING_GOAL",
                tool_name="mission_start",
                suggestion='mission_start(goal="Ship feature X", steps="1. a\\n2. b", verify_command="pytest -q")',
            )
        step_list: list[str] = []
        raw = (steps or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    step_list = [str(x) for x in parsed]
                else:
                    step_list = [ln.strip(" -*\t") for ln in raw.splitlines() if ln.strip()]
            except json.JSONDecodeError:
                step_list = [ln.strip(" -*\t") for ln in raw.splitlines() if ln.strip()]
        m = create_mission(
            g,
            steps=step_list,
            session_id=str(getattr(runtime, "_session_id", "") or "") or None,
            verify_command=(verify_command or "").strip() or None,
            home=_home(),
        )
        return "Mission started.\n" + mission_summary(m)

    async def mission_status(mission_id: str = "") -> str:
        store = MissionStore(_home())
        mid = (mission_id or "").strip()
        m = store.get(mid) if mid else store.latest(
            str(getattr(runtime, "_session_id", "") or "") or None
        )
        if m is None:
            return "No active mission. Use mission_start to create one."
        return mission_summary(m)

    async def mission_update(
        status: str = "done",
        step: str = "",
        note: str = "",
        mission_id: str = "",
    ) -> str:
        store = MissionStore(_home())
        mid = (mission_id or "").strip()
        m = store.get(mid) if mid else store.latest(
            str(getattr(runtime, "_session_id", "") or "") or None
        )
        if m is None:
            return "No mission to update. Call mission_start first."
        st = (status or "done").strip().lower()
        if st not in ("done", "failed", "skipped", "active", "pending"):
            st = "done"
        m = advance_step(m, step_id=(step or None), status=st, note=note or "")
        store.save(m)
        return "Mission updated.\n" + mission_summary(m)

    async def mission_verify(command: str = "", mission_id: str = "") -> str:
        store = MissionStore(_home())
        mid = (mission_id or "").strip()
        m = store.get(mid) if mid else store.latest(
            str(getattr(runtime, "_session_id", "") or "") or None
        )
        cmd = (command or "").strip() or (m.verify_command if m else "") or ""
        if not cmd:
            return format_tool_error(
                "No verify command — pass command= or set verify_command on mission_start",
                code="MISSING_COMMAND",
                tool_name="mission_verify",
            )
        job = await run_job(runtime, "verify", command=cmd)
        if m is not None:
            m.verify_command = cmd
            m.verify_status = "passed" if job.ok else "failed"
            m.last_verify_output = job.summary[:2000]
            if not job.ok:
                m.retries += 1
                if m.retries >= m.max_retries:
                    m.status = "blocked"
            elif m.status == "active" and all(
                s.status in ("done", "skipped") for s in m.steps
            ):
                m.status = "completed"
            store.save(m)
            return (
                f"Verify {'PASSED' if job.ok else 'FAILED'}.\n"
                + mission_summary(m)
                + "\n\n"
                + job.summary[:3000]
            )
        return job.summary

    async def job_run(
        kind: str = "explore",
        query: str = "",
        path: str = ".",
        command: str = "",
    ) -> str:
        """Silent internal job (explore|verify). Returns summary to parent only."""
        result = await run_job(
            runtime,
            kind,
            query=query,
            path=path,
            command=command,
        )
        header = f"[job {result.kind} {'ok' if result.ok else 'failed'}]\n"
        return header + result.summary

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "mission_start",
        "Start a durable work mission: goal + checklist + optional verify_command "
        "(e.g. pytest -q). Use for work-alone multi-step builds.",
        mission_start,
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "steps": {
                    "type": "string",
                    "description": "Newline list or JSON array of step titles",
                },
                "verify_command": {
                    "type": "string",
                    "description": "Command to prove success (tests/build)",
                },
            },
            "required": ["goal"],
        },
    )
    reg.register_builtin_handler(
        "mission_status",
        "Show current mission checklist and verify status.",
        mission_status,
        {
            "type": "object",
            "properties": {"mission_id": {"type": "string"}},
        },
    )
    reg.register_builtin_handler(
        "mission_update",
        "Mark a mission step done/failed/skipped and advance the checklist.",
        mission_update,
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "done | failed | skipped | active",
                },
                "step": {"type": "string", "description": "Step id or title"},
                "note": {"type": "string"},
                "mission_id": {"type": "string"},
            },
        },
    )
    reg.register_builtin_handler(
        "mission_verify",
        "Run the mission verify command (or command=) and record pass/fail.",
        mission_verify,
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "mission_id": {"type": "string"},
            },
        },
    )
    reg.register_builtin_handler(
        "job_run",
        "Run a silent internal job: kind=explore (list+search) or verify (shell tests). "
        "Not a separate chat agent — returns a summary for you to use.",
        job_run,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "explore | verify | implement",
                },
                "query": {"type": "string", "description": "Search query for explore"},
                "path": {"type": "string", "description": "Subpath for explore"},
                "command": {"type": "string", "description": "Shell command for verify"},
            },
            "required": ["kind"],
        },
    )
