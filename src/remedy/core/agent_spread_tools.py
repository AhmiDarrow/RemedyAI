"""Register spread_run — silent parallel workers for covering ground."""

from __future__ import annotations

import json
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.core.spread.planner import plan_spread
from remedy.core.spread.runner import run_spread, spread_depth
from remedy.core.spread.types import SpreadTask


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    # Models sometimes pass accidental lists for string fields
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    return default if value == "" else str(value)


def _parse_tasks_arg(tasks: Any) -> tuple[list[Any] | None, str | None]:
    """Accept native JSON array (preferred by tool-calling models) or a JSON string.

    Returns (parsed_list, error_message). parsed_list is None when empty/absent.
    """
    if tasks is None or tasks == "" or tasks == []:
        return None, None
    if isinstance(tasks, list):
        return tasks, None
    if isinstance(tasks, dict):
        # Single task object → wrap
        return [tasks], None
    if isinstance(tasks, str):
        raw = tasks.strip()
        if not raw:
            return None, None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"tasks must be JSON array: {e}"
        if isinstance(parsed, dict):
            return [parsed], None
        if isinstance(parsed, list):
            return parsed, None
        return None, "tasks must be a JSON array of worker objects"
    return None, f"tasks must be a JSON array (got {type(tasks).__name__})"


def register_spread_tools(runtime: Any) -> None:
    """Register spread_run on the runtime tool registry."""

    async def spread_run(
        goal: str = "",
        tasks: Any = "",
        max_workers: float = 4.0,
        path: str = ".",
    ) -> str:
        """Fan out silent workers (explore/search/verify/diff). Returns merged digest.

        Prefer when ≥2 independent modules/paths. Not a second chat persona.
        Workers never spawn further workers (depth=1).

        ``tasks`` may be a JSON string **or** a native list (models often pass
        arrays via function-calling — never call .strip() on that).
        """
        if spread_depth() >= 1:
            return format_tool_error(
                "spread_run cannot nest inside a worker",
                code="SPREAD_DEPTH",
                tool_name="spread_run",
                suggestion="Parent agent only — workers return digests.",
            )

        goal = _coerce_str(goal, "")
        path = _coerce_str(path, ".") or "."

        # Config knobs
        enabled = True
        max_w = 4
        max_tasks = 6
        use_local = True
        try:
            from remedy.interfaces.config import load_config

            cfg = load_config() or {}
            raw_sp = cfg.get("spread") if isinstance(cfg, dict) else None
            sp: dict = raw_sp if isinstance(raw_sp, dict) else {}
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

        parsed_items, parse_err = _parse_tasks_arg(tasks)
        if parse_err:
            return format_tool_error(
                parse_err,
                code="INVALID_TASKS",
                tool_name="spread_run",
                suggestion=(
                    'tasks=[{"id":"t1","kind":"explore","path":"src/a"},'
                    '{"id":"t2","kind":"explore","path":"src/b"}] '
                    "(native array or JSON string)"
                ),
            )

        task_list: list[SpreadTask] = []
        if parsed_items is not None:
            if not parsed_items:
                return format_tool_error(
                    "tasks must be a non-empty JSON array",
                    code="INVALID_TASKS",
                    tool_name="spread_run",
                )
            for i, item in enumerate(parsed_items[:max_tasks]):
                if isinstance(item, dict):
                    if path and path not in (".",) and not item.get("path"):
                        item = {**item, "path": path}
                    task_list.append(SpreadTask.from_dict(item, index=i))
                elif isinstance(item, str) and item.strip():
                    # Bare path string → explore worker
                    task_list.append(
                        SpreadTask(
                            id=f"t{i + 1}",
                            kind="explore",
                            goal=goal or f"Explore {item}",
                            path=item.strip(),
                        )
                    )
            if len(task_list) < 1:
                return format_tool_error(
                    "tasks array had no valid worker objects",
                    code="INVALID_TASKS",
                    tool_name="spread_run",
                    suggestion='Each item needs kind + path, e.g. {"kind":"explore","path":"src"}',
                )
            # Single explicit task → still run (degraded fan-out of one)
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

        # Auto-plan from goal=
        g = goal.strip()
        if not g:
            return format_tool_error(
                "goal or tasks required",
                code="MISSING_GOAL",
                tool_name="spread_run",
                suggestion=(
                    'spread_run(goal="review auth and api modules") or '
                    "tasks= array of workers"
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
                task_list = [
                    SpreadTask.from_dict(t, index=i)
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

        force_spread = bool(getattr(runtime, "_force_spread", False))
        if not force_spread:
            try:
                from remedy.core.metabolism.governor import get_governor
                from remedy.core.turn_context import turn_session_id

                sid = str(turn_session_id(runtime) or getattr(runtime, "_session_id", "") or "")
                force_spread = bool(get_governor(sid).force_spread)
            except Exception:
                pass
        plan = plan_spread(
            g,
            intent=intent,
            project_path=proj,
            # Local refine only here (opt-in tool path), never on every chat turn
            use_local=use_local,
            max_tasks=max_tasks,
            force=force_spread,
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

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "spread_run",
        "Fan out silent parallel workers to cover independent ground faster. "
        "kind per task: explore | search | verify | diff | review. "
        "Pass tasks as a JSON array (native list or string), or goal= for auto-plan. "
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
                    # Accept array OR string — models pass native arrays via tool_calls
                    "description": (
                        "Worker list: array of "
                        '{"id","kind","goal","path","query","command"} '
                        "or the same as a JSON string. "
                        "kinds: explore|search|verify|diff|review"
                    ),
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "kind": {
                                        "type": "string",
                                        "enum": [
                                            "explore",
                                            "search",
                                            "verify",
                                            "diff",
                                            "read_map",
                                            "review",
                                        ],
                                    },
                                    "goal": {"type": "string"},
                                    "path": {"type": "string"},
                                    "query": {"type": "string"},
                                    "command": {"type": "string"},
                                },
                            },
                        },
                        {"type": "string"},
                    ],
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
