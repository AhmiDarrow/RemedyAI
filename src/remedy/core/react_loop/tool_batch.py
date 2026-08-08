"""Post-tool-batch bookkeeping for the ReAct loop.

Keeps the main loop readable: after execute_tool_calls, update counters,
productivity, phase nudges, and optional build-engine gates.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from remedy.core.react_policy import is_productive_tool_batch
from remedy.core.react_turn import extract_tool_names, extract_write_paths

logger = logging.getLogger(__name__)


def record_tool_batch_stats(
    *,
    turn: Any,
    fresh_calls: list[Any],
    batch_tool_msgs: list[dict[str, Any]],
    step: int,
) -> tuple[int, int]:
    """Update turn stats. Returns (tool_batches_this_turn_delta, productive_delta).

    Caller still owns epoch counters; this returns 1 batch and 0/1 productive.
    """
    try:
        from remedy.core.logging import hot_debug_enabled

        if hot_debug_enabled():
            logger.debug(
                "ReAct step %d executed %d tool call(s)",
                step + 1,
                len(fresh_calls),
            )
    except Exception:
        pass

    turn.record_tool_batch(
        extract_tool_names(fresh_calls),
        paths=extract_write_paths(fresh_calls),
    )
    productive = 1 if is_productive_tool_batch(batch_tool_msgs) else 0
    return 1, productive


def inject_phase_nudge(turn: Any, messages: list[dict[str, Any]]) -> None:
    """Task-loop phase nudge (RESEARCH → PLAN → BUILD) when inject budget allows."""
    with suppress(Exception):
        pn = turn.phase_nudge()
        if pn and turn.inject_count <= turn.max_injects:
            messages.append(pn)


def apply_build_engine_after_batch(
    *,
    runtime: Any,
    turn: Any,
    messages: list[dict[str, Any]],
    fresh_calls: list[Any],
    batch_tool_msgs: list[dict[str, Any]],
    rearm_agency: Any,
) -> list[str]:
    """Run syntax/import gates + observe. Returns status yield strings (@@status…)."""
    status_events: list[str] = []
    with suppress(Exception):
        from remedy.core.build_engine import get_build_state, observe_tool_batch
        from remedy.core.build_syntax import (
            check_paths_syntax,
            format_syntax_gate_message,
        )

        bst = get_build_state(runtime)
        if bst is None or not bst.active:
            return status_events

        observe_tool_batch(bst, fresh_calls, batch_tool_msgs)
        if not bst.write_set:
            return status_events

        syn = check_paths_syntax(list(bst.write_set)[-8:])
        bad = [r for r in syn if not r.get("ok")]
        bst.syntax_ok = not bad
        if bad:
            sm = format_syntax_gate_message(syn)
            if sm is not None:
                messages.append(sm)
                rearm_agency()
                status_events.append("@@status:Build syntax gate red\n")
            return status_events

        with suppress(Exception):
            from remedy.core.build_import_graph import (
                dry_run_imports_for_paths,
                format_import_dry_run_message,
                mutation_score_paths,
            )

            root_p = runtime.effective_project_path()
            py_paths = [
                p for p in list(bst.write_set)[-8:] if str(p).endswith(".py")
            ]
            if py_paths:
                imp = dry_run_imports_for_paths(py_paths, root_p)
                imsg = format_import_dry_run_message(imp)
                if imsg is not None:
                    bst.syntax_ok = False
                    messages.append(imsg)
                    rearm_agency()
                    status_events.append("@@status:Build import dry-run red\n")
                else:
                    with suppress(Exception):
                        ms = mutation_score_paths(root_p, list(bst.write_set))
                        bst.last_mutation_score = ms  # type: ignore[attr-defined]
                        if ms.get("cone_mods"):
                            status_events.append(
                                f"@@status:Build mutation cone {len(ms.get('cone_mods') or [])} mods\n"
                            )
    return status_events
