"""Post-tool-batch bookkeeping for the ReAct loop."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from remedy.core.react_policy import is_productive_tool_batch
from remedy.core.react_turn import extract_tool_names, extract_write_paths

logger = logging.getLogger(__name__)


@contextmanager
def _soft(stage: str) -> Iterator[None]:
    """A best-effort stage: it may fail, but it may not fail *silently*.

    Every step below is optional — the turn must survive a build-engine that
    is half-configured or mid-refactor. Plain ``suppress(Exception)`` bought
    that at the price of making a typo in any of them invisible forever, which
    is how a dead code path can sit here for months looking healthy. Same
    tolerance, with a debug line naming the stage that fell over.
    """
    try:
        yield
    except Exception:
        logger.debug("build engine stage %r failed", stage, exc_info=True)


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
    with _soft("phase-nudge"):
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
    with _soft("build-engine"):
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
            # Explore thrash + zero writes → machine starts TDD / hops
            # instead of only injecting a FORCE IMPLEMENT essay.
            with _soft("auto-implement-drive"):
                from remedy.core.build_drive import (
                    format_drive_message,
                    maybe_auto_implement,
                    should_use_live_llm,
                )

                driven = None
                from remedy.core.build_delta import allow_background_drive
                from remedy.core.build_engine import can_machine_inject

                if allow_background_drive(bst) and can_machine_inject(
                    bst, consume=False
                ):
                    driven = maybe_auto_implement(
                        runtime, bst, use_llm=should_use_live_llm(runtime)
                    )
                if driven:
                    can_machine_inject(bst, consume=True)
                    live = get_build_state(runtime)
                    if live is not None:
                        bst = live
                    dmsg = format_drive_message(driven)
                    if dmsg is not None:
                        messages.append(dmsg)
                    rearm_agency()
                    yield (
                        "@@status:Build machine drive "
                        f"{'ok' if driven.get('ok') else 'partial'}\n"
                    )
            # Post-write blast-radius digest (mapped tests / import cone)
            # so the model verifies the right nodes instead of the whole suite.
            with _soft("write-review"):
                from remedy.core.build_drive import review_write_set

                wrote_now = any(
                    (
                        (t.get("function") or {}).get("name")
                        if isinstance(t, dict)
                        else ""
                    )
                    in {
                        "file_write",
                        "file_edit",
                        "file_edit_batch",
                        "apply_patch",
                        "build_unit_hop",
                        "build_drive",
                        "build_parallel",
                    }
                    for t in (fresh_calls or [])
                    if isinstance(t, dict)
                )
                if (
                    wrote_now
                    and bst.write_set
                    and "write_review" not in bst.nudges_emitted
                    and not should_auto_verify(bst)
                ):
                    rev = review_write_set(runtime, list(bst.write_set))
                    tests = rev.get("tests") or []
                    if tests or rev.get("cone"):
                        bst.nudges_emitted.append("write_review")
                        mapped = ", ".join(str(t) for t in tests[:8]) or "(none yet)"
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "[Build engine · WRITE REVIEW]\n"
                                    f"{rev.get('message')}\n"
                                    f"Mapped tests: {mapped}\n"
                                    "Prefer a scoped verify on those tests "
                                    "(not a whole-repo suite, not a monologue)."
                                ),
                            }
                        )
                        yield "@@status:Build write review\n"
            # Post-write syntax gate (py/json) before full suite
            if bst.write_set:
                from remedy.core.build_syntax import resolve_write_paths

                resolved = resolve_write_paths(runtime, list(bst.write_set)[-8:])
                syn = check_paths_syntax(resolved) if resolved else []
                bad = [r for r in syn if not r.get("ok")]
                # Unresolved paths are skipped, not red — do not block verify
                bst.syntax_ok = (not bad) if resolved else None
                if bad:
                    sm = format_syntax_gate_message(syn)
                    if sm is not None:
                        messages.append(sm)
                        rearm_agency()
                        yield "@@status:Build syntax gate red\n"
                # Import-graph dry-run (faster than pytest for .py)
                if not bad:
                    with _soft("import-dry-run"):
                        from remedy.core.build_import_graph import (
                            dry_run_imports_for_paths,
                            format_import_dry_run_message,
                            mutation_score_paths,
                        )

                        root_p = runtime.effective_project_path()
                        py_paths = [
                            p
                            for p in resolved
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
                                with _soft("mutation-score"):
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
                    with _soft("seed-oracle"):
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
                    # Mark turn green so the loop can short-summary without re-tooling
                    # (play/ship goals keep agency — see keep_agency_after_green).
                    with _soft("keep-agency-after-green"):
                        from remedy.core.build_engine import keep_agency_after_green

                        if not keep_agency_after_green(bst):
                            from remedy.core.turn_context import (
                                set_turn_build_verify_green,
                            )

                            set_turn_build_verify_green(True, runtime)
                    from remedy.core.build_engine import (
                        format_ship_report_line,
                        green_continue_message,
                    )

                    # Ship / play: continue with tools; else short summary only
                    gmsg = green_continue_message(
                        bst, command=str(av.get("command") or "")
                    )
                    messages.append(gmsg)
                    if bst.ship_required and not bst.ship_complete():
                        rearm_agency()
                        yield "@@status:Build green — continue ship\n"
                    else:
                        with _soft("keep-agency-after-verify"):
                            from remedy.core.build_engine import keep_agency_after_green

                            if keep_agency_after_green(bst):
                                rearm_agency()
                                yield "@@status:Build green — play/iterate\n"
                    ship_line = format_ship_report_line(bst)
                    if ship_line:
                        yield ship_line
                    # Second pass over the write set (todos, bare except, syntax)
                    with _soft("live-llm-check"):
                        from remedy.core.build_drive import should_use_live_llm
                        from remedy.core.build_review_fix import (
                            format_review_fix_message,
                            maybe_review_fix,
                        )

                        rf = maybe_review_fix(
                            runtime, bst, use_llm=should_use_live_llm(runtime)
                        )
                        if rf:
                            rmsg = format_review_fix_message(rf)
                            if rmsg is not None:
                                messages.append(rmsg)
                            yield (
                                "@@status:Build review-fix "
                                f"err={rf.get('errors')} warn={rf.get('warns')}\n"
                            )
                            if rf.get("errors") and not rf.get("ok"):
                                rearm_agency()
                    with _soft("machine-nudge"):
                        from remedy.core.build_engine import can_machine_inject
                        from remedy.core.companion_observe import (
                            append_observe_messages,
                            maybe_visual_observe,
                        )

                        if can_machine_inject(bst, consume=False):
                            vis = maybe_visual_observe(runtime, bst)
                            if vis:
                                can_machine_inject(bst, consume=True)
                                # Flush here: loop.py already flushed CUA shots
                                # before this after-batch hook.
                                append_observe_messages(runtime, vis, messages)
                                rearm_agency()
                                yield (
                                    "@@status:Build visual observe "
                                    f"{'ok' if vis.get('ok') else 'miss'}\n"
                                )
                    # D: optional mutant kill sample after green (cheap) — skip on local
                    # to avoid another long tool wave after done.
                elif av.get("blocked") or av.get("approval"):
                    logger.info(
                        "Build auto-verify blocked (approval/jail) cmd=%s",
                        av.get("command"),
                    )
                    rearm_agency()
                    yield "@@status:Build auto-verify blocked — needs approval\n"
                else:
                    if "auto_verify_repair" not in bst.nudges_emitted:
                        bst.nudges_emitted.append("auto_verify_repair")
                    # Allow another cycle after next writes
                    bst.auto_verify_ran = False
                    # C: schedule repair queue from error vector
                    with _soft("repair-queue"):
                        from remedy.core.build_repair_queue import (
                            queue_from_error_vector,
                        )

                        q = queue_from_error_vector(
                            getattr(bst, "last_error_vector", None) or {},
                            write_set=list(bst.write_set or []),
                            root=runtime.effective_project_path(),
                        )
                        bst.repair_queue = q.to_public()  # type: ignore[attr-defined]
                        if q.targets:
                            yield (
                                "@@status:Build repair queue "
                                f"{len(q.targets)} targets\n"
                            )
                    # Machine hops the queue — do not wait for the model
                    # to remember build_repair_queue(run_hops=true).
                    with _soft("build-drive-format"):
                        from remedy.core.build_drive import (
                            format_drive_message,
                            maybe_auto_repair,
                            should_use_live_llm,
                        )

                        hopped = maybe_auto_repair(
                            runtime, bst, use_llm=should_use_live_llm(runtime)
                        )
                        if hopped:
                            hmsg = format_drive_message(hopped)
                            if hmsg is not None:
                                messages.append(hmsg)
                            yield (
                                "@@status:Build auto-repair "
                                f"ran={hopped.get('ran')} "
                                f"{'ok' if hopped.get('ok') else 'red'}"
                                f"{' cap' if hopped.get('capped') else ''}\n"
                            )
                    logger.info(
                        "Build auto-verify RED cmd=%s scoped=%s",
                        av.get("command"),
                        av.get("scoped"),
                    )
                    rearm_agency()
                # Green: do not rearm tools (summary-only). Red/cap: already rearmed above.
                if not av.get("ok") and not av.get("capped"):
                    pass  # rearmed in red branch
                elif av.get("capped") or av.get("oracle_missing"):
                    rearm_agency()
                _av_label = (
                    "green"
                    if av.get("ok")
                    else (
                        "cap"
                        if av.get("capped")
                        else (
                            "blocked"
                            if av.get("blocked") or av.get("approval")
                            else "red"
                        )
                    )
                )
                yield (
                    f"@@status:Build auto-verify {_av_label}"
                    f"{' scoped' if av.get('scoped') else ''}"
                    f"{' seeded' if av.get('seeded') else ''}"
                    f"{' (oracle missing)' if av.get('oracle_missing') else ''}\n"
                )
            elif bst.syntax_ok is not False:
                from remedy.core.build_delta import collapse_to_one_card

                mnudge = next_machine_nudge(bst)
                if mnudge is not None and collapse_to_one_card(messages, mnudge):
                    rearm_agency()
                    logger.info(
                        "Build engine nudge phase=%s explore=%d write=%d verify=%d",
                        bst.phase,
                        bst.explore_steps,
                        bst.write_steps,
                        bst.verify_steps,
                    )
            # Persist ledger every batch
            with _soft("home-beacon"):
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
