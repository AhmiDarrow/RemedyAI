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

    def _suggest_verify(path: str = "") -> str | None:
        try:
            from remedy.core.project_fingerprint import fingerprint_path

            if (path or "").strip():
                root = runtime.resolve_tool_path(path)
            else:
                root = runtime.effective_project_path()
            if root.is_file():
                root = root.parent
            return fingerprint_path(root).suggest_verify
        except Exception:
            return None

    async def mission_start(
        goal: str = "",
        steps: str = "",
        verify_command: str = "",
        path: str = "",
    ) -> str:
        """Start a durable mission (checklist + optional verify command)."""
        if isinstance(steps, list | dict):
            steps = json.dumps(steps, ensure_ascii=False, default=str)
        g = str(goal or "").strip()
        if not g:
            return format_tool_error(
                "goal is required",
                code="MISSING_GOAL",
                tool_name="mission_start",
                suggestion=(
                    'mission_start(goal="Ship feature X", steps="1. a\\n2. b", '
                    'verify_command="pytest -q")'
                ),
            )
        step_list: list[str] = []
        raw = str(steps or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    step_list = [str(x) for x in parsed]
                else:
                    step_list = [ln.strip(" -*\t") for ln in raw.splitlines() if ln.strip()]
            except json.JSONDecodeError:
                step_list = [ln.strip(" -*\t") for ln in raw.splitlines() if ln.strip()]

        vcmd = (verify_command or "").strip()
        auto_note = ""
        if not vcmd:
            suggested = _suggest_verify(path)
            if suggested:
                vcmd = suggested
                auto_note = f"\n(auto verify_command from stack fingerprint: {vcmd})"

        m = create_mission(
            g,
            steps=step_list,
            session_id=str(getattr(runtime, "_session_id", "") or "") or None,
            verify_command=vcmd or None,
            home=_home(),
        )
        return "Mission started.\n" + mission_summary(m) + auto_note

    async def mission_status(mission_id: str = "") -> str:
        store = MissionStore(_home())
        mid = (mission_id or "").strip()
        m = store.get(mid) if mid else store.latest(
            str(getattr(runtime, "_session_id", "") or "") or None
        )
        if m is None:
            return "No active mission. Use mission_start to create one."
        extra = ""
        if m.status == "active" and m.verify_command and m.verify_status != "passed":
            if all(s.status in ("done", "skipped") for s in m.steps):
                extra = (
                    "\n\nNote: all checklist steps done but verify not passed — "
                    "run mission_verify before claiming complete."
                )
        return mission_summary(m) + extra

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
        done_gate = ""
        if (
            m.status == "active"
            and m.verify_command
            and m.verify_status != "passed"
            and all(s.status in ("done", "skipped") for s in m.steps)
        ):
            done_gate = (
                "\n\nDo not claim done yet — run mission_verify "
                f"({m.verify_command!r}) first."
            )
        return "Mission updated.\n" + mission_summary(m) + done_gate

    async def mission_verify(
        command: str = "",
        mission_id: str = "",
        path: str = "",
        timeout_seconds: float = 180.0,
    ) -> str:
        store = MissionStore(_home())
        mid = (mission_id or "").strip()
        m = store.get(mid) if mid else store.latest(
            str(getattr(runtime, "_session_id", "") or "") or None
        )
        cmd = (command or "").strip() or (m.verify_command if m else "") or ""
        if not cmd:
            suggested = _suggest_verify(path)
            if suggested:
                cmd = suggested
        if not cmd:
            return format_tool_error(
                "No verify command — pass command= or set verify_command on mission_start",
                code="MISSING_COMMAND",
                tool_name="mission_verify",
                suggestion='mission_verify(command="pytest -q") or path= to a known tree',
            )
        try:
            timeout = float(timeout_seconds if timeout_seconds is not None else 180.0)
        except (TypeError, ValueError):
            timeout = 180.0
        job = await run_job(
            runtime,
            "verify",
            command=cmd,
            path=path or ".",
            timeout=timeout,
        )
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
            out = (
                f"Verify {'PASSED' if job.ok else 'FAILED'}.\n"
                + mission_summary(m)
                + "\n\n"
                + job.summary[:3000]
            )
            # Git hygiene snapshot after successful verify
            if job.ok:
                try:
                    diff = await run_job(runtime, "diff", path=path or ".")
                    out += "\n\n--- git after verify ---\n" + diff.summary[:2500]
                except Exception:
                    pass
            return out
        return job.summary

    async def job_run(
        kind: str = "explore",
        query: str = "",
        path: str = ".",
        command: str = "",
        timeout_seconds: float = 180.0,
    ) -> str:
        """Silent internal job (explore|verify|diff). Returns summary to parent only."""
        try:
            timeout = float(timeout_seconds if timeout_seconds is not None else 180.0)
        except (TypeError, ValueError):
            timeout = 180.0
        result = await run_job(
            runtime,
            kind,
            query=query,
            path=path,
            command=command,
            timeout=timeout,
        )
        header = f"[job {result.kind} {'ok' if result.ok else 'failed'}]\n"
        return header + result.summary

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "mission_start",
        "Start a durable work mission: goal + checklist + optional verify_command "
        "(e.g. pytest -q). If verify_command is empty, may auto-fill from stack "
        "fingerprint of focus folder or path=. Use for work-alone multi-step builds.",
        mission_start,
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "steps": {
                    "description": "Newline list or JSON array of step titles",
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    ],
                },
                "verify_command": {
                    "type": "string",
                    "description": "Command to prove success (tests/build)",
                },
                "path": {
                    "type": "string",
                    "description": "Optional tree for stack fingerprint / verify default",
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
        "Run the mission verify command (or command= / fingerprint default) and "
        "record pass/fail. Prefer this before claiming multi-step work done.",
        mission_verify,
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "mission_id": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Working directory for verify (absolute or relative)",
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "Timeout seconds (default 180, max 600)",
                    "default": 180,
                },
            },
        },
    )
    reg.register_builtin_handler(
        "job_run",
        "Run a silent internal job: kind=explore (tree+fingerprint+search), "
        "verify (shell tests), or diff (git status/stat). "
        "Not a separate chat agent — returns a summary for you to use. "
        "path= may be absolute for multi-tree work.",
        job_run,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "explore | verify | diff | implement",
                },
                "query": {"type": "string", "description": "Search query for explore"},
                "path": {
                    "type": "string",
                    "description": "Directory for explore/verify/diff (absolute OK)",
                },
                "command": {"type": "string", "description": "Shell command for verify"},
                "timeout_seconds": {
                    "type": "number",
                    "description": "Timeout for verify jobs (default 180)",
                    "default": 180,
                },
            },
            "required": ["kind"],
        },
    )
