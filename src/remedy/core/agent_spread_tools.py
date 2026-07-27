"""Register spread_run — silent parallel workers for covering ground."""

from __future__ import annotations

import json
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.core.spread.planner import plan_spread
from remedy.core.spread.runner import run_spread, spread_depth
from remedy.core.spread.types import SpreadTask


def register_spread_tools(runtime: Any) -> None:
    """Register spread_run on the runtime tool registry."""

    async def spread_run(
        goal: str = "",
        tasks: str = "",
        max_workers: float = 4.0,
        path: str = ".",
    ) -> str:
        """Fan out silent workers (explore/search/verify/diff). Returns merged digest.

        Prefer when ≥2 independent modules/paths. Not a second chat persona.
        Workers never spawn further workers (depth=1).
        """
        if spread_depth() >= 1:
            return format_tool_error(
                "spread_run cannot nest inside a worker",
                code="SPREAD_DEPTH",
                tool_name="spread_run",
                suggestion="Parent agent only — workers return digests.",
            )

        # Config knobs
        enabled = True
        max_w = 4
        max_tasks = 6
        use_local = True
        try:
            from remedy.interfaces.config import load_config

            cfg = load_config() or {}
            sp = cfg.get("spread") if isinstance(cfg.get("spread"), dict) else {}
            if sp.get("enabled") is False:
                enabled = False
            if sp.get("max_workers") is not None:
                max_w = int(sp.get("max_workers") or 4)
            if sp.get("max_tasks") is not None:
                max_tasks = int(sp.get("max_tasks") or 6)
            if sp.get("use_local_plan") is False:
                use_local = False
        except Exception:
            pass
        # Hard caps — never let config explode fan-out
        max_tasks = max(2, min(12, int(max_tasks or 6)))
        max_w = max(1, min(8, int(max_w or 4)))
        if not enabled:
            return format_tool_error(
                "spread is disabled in config (spread.enabled=false)",
                code="SPREAD_DISABLED",
                tool_name="spread_run",
            )

        try:
            mw = int(max_workers if max_workers is not None else max_w)
        except (TypeError, ValueError):
            mw = max_w
        mw = max(1, min(8, mw, max_w))

        task_list: list[SpreadTask] = []
        raw_tasks = (tasks or "").strip()
        if raw_tasks:
            try:
                parsed = json.loads(raw_tasks)
            except json.JSONDecodeError as e:
                return format_tool_error(
                    f"tasks must be JSON array: {e}",
                    code="INVALID_TASKS",
                    tool_name="spread_run",
                    suggestion=(
                        'tasks=\'[{"id":"t1","kind":"explore","path":"src/a"},'
                        '{"id":"t2","kind":"explore","path":"src/b"}]\''
                    ),
                )
            if not isinstance(parsed, list) or not parsed:
                return format_tool_error(
                    "tasks must be a non-empty JSON array",
                    code="INVALID_TASKS",
                    tool_name="spread_run",
                )
            for i, item in enumerate(parsed[:max_tasks]):
                if isinstance(item, dict):
                    if path and path not in (".",) and not item.get("path"):
                        item = {**item, "path": path}
                    task_list.append(SpreadTask.from_dict(item, index=i))
        else:
            g = (goal or "").strip()
            if not g:
                return format_tool_error(
                    "goal or tasks required",
                    code="MISSING_GOAL",
                    tool_name="spread_run",
                    suggestion=(
                        'spread_run(goal="review auth and api modules") or '
                        "tasks= JSON array of workers"
                    ),
                )
            intent = "tool"
            try:
                snap = getattr(runtime, "_last_context_snapshot", None)
                if snap is not None and getattr(snap, "intent", None):
                    intent = str(snap.intent)
            except Exception:
                pass
            proj = "."
            try:
                if callable(getattr(runtime, "effective_project_path", None)):
                    proj = str(runtime.effective_project_path())
            except Exception:
                proj = "."
            # Prefer fresh snapshot plan (already computed on the turn — free).
            plan = None
            try:
                snap = getattr(runtime, "_last_context_snapshot", None)
                sig = getattr(snap, "signals", None) or {}
                sp_pub = sig.get("spread") if isinstance(sig, dict) else None
                if (
                    isinstance(sp_pub, dict)
                    and sp_pub.get("spread")
                    and isinstance(sp_pub.get("tasks"), list)
                    and len(sp_pub["tasks"]) >= 2
                ):
                    from remedy.core.spread.types import SpreadTask as _ST

                    task_list = [
                        _ST.from_dict(t, index=i)
                        for i, t in enumerate(sp_pub["tasks"][:max_tasks])
                        if isinstance(t, dict)
                    ]
                    if len(task_list) >= 2:
                        reason = str(sp_pub.get("reason") or "snapshot_plan")
                        result = await run_spread(
                            runtime,
                            task_list,
                            max_workers=mw,
                            reason=reason,
                        )
                        header = (
                            f"[spread_run ok={result.ok} workers={len(result.results)} "
                            f"wall_ms={result.wall_ms:.0f} method_plan=snapshot]\n"
                        )
                        return header + result.merged_summary
            except Exception:
                pass

            plan = plan_spread(
                g,
                intent=intent,
                project_path=proj,
                # Local refine only here (opt-in tool path), never on every chat turn
                use_local=use_local,
                max_tasks=max_tasks,
            )
            if not plan.spread or len(plan.tasks) < 2:
                # Cheap single explore — do NOT fake 2-worker same-path fan-out
                base_path = (path or ".").strip() or "."
                from remedy.core.jobs import run_job

                single = await run_job(
                    runtime,
                    "explore",
                    query=g[:200],
                    path=base_path,
                )
                return (
                    f"[spread_run degraded=single_explore reason={plan.reason}]\n"
                    "Planner did not partition into independent branches — "
                    "ran one explore instead of parallel workers.\n\n"
                    + single.summary
                )
            task_list = plan.tasks
            reason = plan.reason
            result = await run_spread(
                runtime,
                task_list,
                max_workers=mw,
                reason=reason,
            )
            header = (
                f"[spread_run ok={result.ok} workers={len(result.results)} "
                f"wall_ms={result.wall_ms:.0f} method_plan={plan.method}]\n"
            )
            return header + result.merged_summary

        reason = "explicit_tasks"
        result = await run_spread(
            runtime,
            task_list,
            max_workers=mw,
            reason=reason,
        )
        header = (
            f"[spread_run ok={result.ok} workers={len(result.results)} "
            f"wall_ms={result.wall_ms:.0f}]\n"
        )
        return header + result.merged_summary

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "spread_run",
        "Fan out silent parallel workers to cover independent ground faster. "
        "kind per task: explore | search | verify | diff | review. "
        "Pass tasks= JSON array, or goal= for auto-plan. "
        "Returns one merged digest (not multi-agent chat). "
        "Use when ≥2 modules/paths; not for pure chat or single-file edits. "
        "Workers cannot nest. Prefer over many serial list_dir/file_read loops.",
        spread_run,
        {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "High-level goal for auto task planning",
                },
                "tasks": {
                    "type": "string",
                    "description": (
                        'JSON array: [{"id","kind","goal","path","query","command"}] '
                        "kinds: explore|search|verify|diff|review"
                    ),
                },
                "max_workers": {
                    "type": "number",
                    "description": "Max concurrent workers (default 4, max 8)",
                    "default": 4,
                },
                "path": {
                    "type": "string",
                    "description": "Default path when tasks omit path",
                },
            },
        },
    )
