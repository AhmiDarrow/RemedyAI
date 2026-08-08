"""Post-tool-batch bookkeeping for the ReAct loop."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
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
    """Update turn stats. Returns (batch_delta, productive_delta)."""
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


async def apply_build_engine_after_batch(
    *,
    runtime: Any,
    messages: list[dict[str, Any]],
    fresh_calls: list[Any],
    batch_tool_msgs: list[dict[str, Any]],
    rearm_agency: Any,
) -> AsyncIterator[str]:
    """Run syntax/import gates, auto-verify, machine nudges. Yields @@status lines."""
    # Machine build engine: syntax gate + auto-verify + force nudges
    with suppress(Exception):
        from remedy.core.build_engine import (
            get_build_state,
            next_machine_nudge,
            observe_tool_batch,
        )
        from remedy.core.build_ledger import merge_turn_into_ledger
        from remedy.core.build_oracle import (
            format_auto_verify_message,
            run_auto_verify,
            should_auto_verify,
        )
        from remedy.core.build_syntax import (
            check_paths_syntax,
            format_syntax_gate_message,
        )
        from remedy.core.turn_context import turn_session_id

        bst = get_build_state(runtime)
        if bst is not None and bst.active:
            observe_tool_batch(bst, fresh_calls, batch_tool_msgs)
            # Post-write syntax gate (py/json) before full suite
            if bst.write_set:
                syn = check_paths_syntax(list(bst.write_set)[-8:])
                bad = [r for r in syn if not r.get("ok")]
                bst.syntax_ok = not bad
                if bad:
                    sm = format_syntax_gate_message(syn)
                    if sm is not None:
                        messages.append(sm)
                        rearm_agency()
                        yield "@@status:Build syntax gate red\n"
                # Import-graph dry-run (faster than pytest for .py)
                if not bad:
                    with suppress(Exception):
                        from remedy.core.build_import_graph import (
                            dry_run_imports_for_paths,
                            format_import_dry_run_message,
                            mutation_score_paths,
                        )

                        root_p = runtime.effective_project_path()
                        py_paths = [
                            p
                            for p in list(bst.write_set)[-8:]
                            if str(p).endswith(".py")
                        ]
                        if py_paths:
                            imp = dry_run_imports_for_paths(
                                py_paths, root_p
                            )
                            imsg = format_import_dry_run_message(imp)
                            if imsg is not None:
                                bst.syntax_ok = False  # block suite
                                messages.append(imsg)
                                rearm_agency()
                                yield "@@status:Build import dry-run red\n"
                            else:
                                # mutation score for next scoped verify
                                with suppress(Exception):
                                    ms = mutation_score_paths(
                                        root_p, list(bst.write_set)
                                    )
                                    bst.last_mutation_score = ms  # type: ignore[attr-defined]
                                    if ms.get("cone_mods"):
                                        yield (
                                            "@@status:Build mutation cone "
                                            f"{len(ms['cone_mods'])} mods "
                                            f"score={ms.get('mutation_score')}\n"
                                        )
            # Machine-owned falsification after write waves
            if should_auto_verify(bst) and bst.syntax_ok is not False:
                if not bst.verify_command:
                    from remedy.core.build_oracle import (
                        discover_verify_command,
                    )

                    bst.verify_command = discover_verify_command(runtime)
                    bst.oracle_ok = bool(bst.verify_command)
                av = await run_auto_verify(runtime, bst)
                # Surface oracle seed if machine planted smoke tests
                seed = getattr(bst, "_seed_message", None)
                if seed and isinstance(seed, dict):
                    with suppress(Exception):
                        from remedy.core.build_seed_oracle import (
                            format_seed_oracle_message,
                        )

                        messages.append(format_seed_oracle_message(seed))
                    bst._seed_message = None  # type: ignore[attr-defined]
                messages.append(
                    format_auto_verify_message(av, state=bst)
                )
                if av.get("oracle_missing"):
                    pass
                elif av.get("capped"):
                    logger.info("Build auto-verify CAP cycles=%s", bst.auto_verify_cycles)
                elif av.get("ok"):
                    logger.info(
                        "Build auto-verify GREEN cmd=%s scoped=%s",
                        av.get("command"),
                        av.get("scoped"),
                    )
                    # D: optional mutant kill sample after green (cheap)
                    with suppress(Exception):
                        if (
                            "mutant_sampled" not in bst.nudges_emitted
                            and bst.write_set
                        ):
                            from remedy.core.build_mutant import (
                                format_mutant_message,
                                mutant_kill_score,
                            )

                            root_m = runtime.effective_project_path()
                            mk = mutant_kill_score(
                                root_m, list(bst.write_set)[-4:]
                            )
                            bst.last_mutant_kill = mk  # type: ignore[attr-defined]
                            if mk.get("total"):
                                bst.nudges_emitted.append("mutant_sampled")
                                messages.append(format_mutant_message(mk))
                                if mk.get("survived"):
                                    yield (
                                        "@@status:Build mutants survived "
                                        f"{mk.get('survived')}\n"
                                    )
                else:
                    if "auto_verify_repair" not in bst.nudges_emitted:
                        bst.nudges_emitted.append("auto_verify_repair")
                    # Allow another cycle after next writes
                    bst.auto_verify_ran = False
                    # C: schedule repair queue from error vector
                    with suppress(Exception):
                        from remedy.core.build_repair_queue import (
                            format_repair_queue_message,
                            queue_from_error_vector,
                        )

                        q = queue_from_error_vector(
                            getattr(bst, "last_error_vector", None) or {},
                            write_set=list(bst.write_set or []),
                            root=runtime.effective_project_path(),
                        )
                        bst.repair_queue = q.to_public()  # type: ignore[attr-defined]
                        if q.targets:
                            messages.append(format_repair_queue_message(q))
                            yield (
                                "@@status:Build repair queue "
                                f"{len(q.targets)} targets\n"
                            )
                    logger.info(
                        "Build auto-verify RED cmd=%s scoped=%s",
                        av.get("command"),
                        av.get("scoped"),
                    )
                rearm_agency()
                yield (
                    f"@@status:Build auto-verify "
                    f"{'green' if av.get('ok') else ('cap' if av.get('capped') else 'red')}"
                    f"{' scoped' if av.get('scoped') else ''}"
                    f"{' seeded' if av.get('seeded') else ''}"
                    f"{' (oracle missing)' if av.get('oracle_missing') else ''}\n"
                )
            elif bst.syntax_ok is not False:
                mnudge = next_machine_nudge(bst)
                if mnudge is not None:
                    messages.append(mnudge)
                    rearm_agency()
                    logger.info(
                        "Build engine nudge phase=%s explore=%d write=%d verify=%d",
                        bst.phase,
                        bst.explore_steps,
                        bst.write_steps,
                        bst.verify_steps,
                    )
            # Persist ledger every batch
            with suppress(Exception):
                home_b = getattr(
                    getattr(runtime, "config", None), "home_dir", None
                )
                proj_b = str(
                    getattr(bst, "project_path", None)
                    or runtime.effective_project_path()
                    or ""
                )
                merge_turn_into_ledger(
                    bst,
                    project_path=proj_b,
                    session_id=str(turn_session_id(runtime) or ""),
                    home=home_b,
                )
