"""Machine-owned implement-verify-fix driver.

The model is muscle. This module is the scheduler:

    compile spec → TDD red → hop units → gate tower → repair hops

Called by:
- ``build_drive`` tool (model can kick it)
- ``apply_build_engine_after_batch`` on explore-thrash or red verify

Quality comes from loop convergence + real oracles, not from one smart
generation — the same thesis as ``docs/RESEARCH_build_reducer_small_models.md``,
now wired to the live turn.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

# Serializes the auto-repair cap's read-check-increment on the shared
# BuildTurnState (concurrent tabs / turn + background paths).
_auto_repair_lock = threading.Lock()

_IMPLEMENT_RE = re.compile(
    r"(?i)\b(implement|fix|build|add|create|write|scaffold|ship|develop)\b"
)
_REVIEW_ONLY_RE = re.compile(
    r"(?i)\b(review|audit|explain|summarize|what does|how does)\b"
)


def goal_wants_machine_implement(goal: str) -> bool:
    """False for review-only asks so auto-drive does not plant TDD tests."""
    g = goal or ""
    if _IMPLEMENT_RE.search(g):
        return True
    return not _REVIEW_ONLY_RE.search(g)


def should_use_live_llm(runtime: Any = None) -> bool:
    """True when hops may call the turn's LLM. Always false under pytest."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    flag = str(os.environ.get("REMEDY_BUILD_DRIVE_LLM", "") or "").strip().lower()
    if flag in {"0", "false", "off", "no"}:
        return False
    if flag in {"1", "true", "on", "yes"}:
        return True
    if runtime is None:
        return False
    with suppress(Exception):
        from remedy.core.llm_binding import get_llm_binding

        bind = get_llm_binding(runtime)
        if bind is not None and (getattr(bind, "model", None) or getattr(bind, "provider", None)):
            return True
    return False


def _project_root(runtime: Any) -> Path | None:
    with suppress(Exception):
        raw = runtime.effective_project_path()
        if not raw:
            return None
        p = Path(raw)
        return p.parent if p.is_file() else p
    return None


def review_write_set(
    runtime: Any,
    write_set: list[str] | None = None,
) -> dict[str, Any]:
    """Cheap post-write digest: mapped tests + import cone (no LLM)."""
    root = _project_root(runtime)
    paths = [str(p) for p in (write_set or []) if p]
    tests: list[str] = []
    cone: list[str] = []
    if root is not None and paths:
        with suppress(Exception):
            from remedy.core.build_scoped import map_source_to_test_candidates

            for w in paths:
                for tp in map_source_to_test_candidates(w.replace("\\", "/"), root):
                    try:
                        tests.append(tp.relative_to(root).as_posix())
                    except Exception:
                        tests.append(str(tp))
        with suppress(Exception):
            from remedy.core.build_import_graph import mutation_score_paths

            ms = mutation_score_paths(root, paths)
            cone = list(ms.get("cone_paths") or [])[:16]
    tests_u: list[str] = []
    seen: set[str] = set()
    for t in tests:
        if t not in seen:
            seen.add(t)
            tests_u.append(t)
    return {
        "paths": paths[-16:],
        "tests": tests_u[:16],
        "cone": cone,
        "message": (
            f"write_review paths={len(paths)} mapped_tests={len(tests_u)} "
            f"cone={len(cone)}"
        ),
    }


def drive_build(
    runtime: Any,
    *,
    goal: str = "",
    use_llm: bool | None = None,
    max_units: int = 8,
    max_repairs: int = 3,
) -> dict[str, Any]:
    """Run the machine loop for *goal* (or the active build-turn goal)."""
    from remedy.core.build_engine import begin_build_turn, get_build_state
    from remedy.core.build_spec_compiler import compile_goal_to_spec, save_locked_spec
    from remedy.core.build_tdd import tdd_bootstrap
    from remedy.core.build_todos import seed_drive_todos, upsert_todos

    st = get_build_state(runtime)
    g = (goal or "").strip() or str(getattr(st, "goal", "") or "")
    if not g:
        return {"ok": False, "error": "goal= required (or active build turn)", "phase": "idle"}
    if use_llm is None:
        use_llm = should_use_live_llm(runtime)
    begin_build_turn(runtime, g, force=True)
    st = get_build_state(runtime)
    root = _project_root(runtime)
    if root is None:
        return {"ok": False, "error": "no project path — set a focus folder", "phase": "idle"}

    compiled = compile_goal_to_spec(g, root=root)
    if not compiled.get("ok"):
        return {
            "ok": False,
            "error": compiled.get("error") or "spec compile failed",
            "phase": "spec",
            "compiled": compiled,
        }
    save_locked_spec(root, compiled)
    units = list(compiled.get("units") or [])[: max(1, min(16, int(max_units or 8)))]
    seed_drive_todos(runtime, units=units, goal=g)
    upsert_todos(
        runtime,
        [{"id": "spec", "content": f"Lock BuildSpec: {g[:80]}", "status": "completed"}],
        merge=True,
    )

    tdd = tdd_bootstrap(runtime, g, use_llm_implement=False)
    upsert_todos(
        runtime,
        [
            {
                "id": "tdd",
                "content": "Write failing TDD tests before implement",
                "status": "completed" if (tdd.get("tdd") or {}).get("written") else "pending",
            }
        ],
        merge=True,
    )

    from remedy.core.build_isolated import parallel_isolated_hops

    prepared: list[dict[str, Any]] = []
    for u in units:
        if not isinstance(u, dict):
            continue
        path = str(u.get("path") or "").replace("\\", "/").strip()
        if not path:
            continue
        item = dict(u)
        item["path"] = path
        item.setdefault("behavior", g[:400])
        prepared.append(item)
    hop_results = parallel_isolated_hops(
        runtime,
        prepared,
        use_llm=bool(use_llm),
        max_repairs=max(1, min(8, int(max_repairs or 3))),
        max_workers=4,
    )
    for res in hop_results:
        path = str(res.get("path") or "")
        upsert_todos(
            runtime,
            [
                {
                    "id": f"unit-{Path(path).stem}"[:24],
                    "content": f"Implement {Path(path).stem} ({path})",
                    "status": "completed" if res.get("ok") else "in_progress",
                }
            ],
            merge=True,
        )

    write_set: list[str] = []
    if st is not None:
        write_set = list(getattr(st, "write_set", None) or [])
    if not write_set:
        write_set = [str(u.get("path")) for u in units if isinstance(u, dict) and u.get("path")]

    gate: dict[str, Any] = {}
    drive_green: dict[str, Any] = {}
    repair: dict[str, Any] = {}
    if write_set:
        with suppress(Exception):
            from remedy.core.build_gate_tower import run_gate_tower
            from remedy.core.build_persist import iterate_to_green_multi

            base = str(getattr(st, "verify_command", "") or "") if st else ""

            def _verify() -> dict[str, Any]:
                g = run_gate_tower(runtime, write_set, base_verify=base)
                # progress = how many gate levels pass (monotone toward green)
                g["progress"] = len(g.get("passed_levels") or [])
                return g

            def _repair_narrow(_v: dict[str, Any]) -> dict[str, Any]:
                if not use_llm:
                    return {"ran": False}
                res = maybe_auto_repair(runtime, st, use_llm=True) or {}
                return {"ran": bool(res.get("ran") or res.get("results")), **res}

            def _repair_broad(_v: dict[str, Any]) -> dict[str, Any]:
                if not use_llm:
                    return {"ran": False}
                res = maybe_auto_repair(runtime, st, use_llm=True, broaden=True) or {}
                return {"ran": bool(res.get("ran") or res.get("results")), **res}

            # Persist with VARIETY: loop verify → repair, and when the narrow
            # source-first fix stalls, rotate to the broadened strategy
            # (tests + more targets) before giving up. Drives to actually
            # passing, from more than one angle.
            gate = _verify()
            if gate.get("ok"):
                drive_green = {"ok": True, "rounds": 0, "reason": "green"}
            else:
                # Strategy selection steered by what has landed green before:
                # if the broadened angle keeps winning, start bold sooner.
                strat_map = {"source-first": _repair_narrow, "broadened": _repair_broad}
                order = ["source-first", "broadened"]
                with suppress(Exception):
                    from remedy.core.build_persist import order_strategy_names
                    from remedy.memory.soul.field import load_soul_field

                    home = getattr(getattr(runtime, "config", None), "home_dir", None)
                    lessons = list(load_soul_field(home).organism_lessons or [])
                    order = order_strategy_names(order, lessons)
                outcome = iterate_to_green_multi(
                    _verify,
                    [(n, strat_map[n]) for n in order if n in strat_map],
                    max_rounds=max(2, min(12, int(max_repairs or 3) + 4)),
                )
                drive_green = outcome.to_public()
                repair = {"ran": sum(1 for h in outcome.history if h.get("repaired"))}
                if outcome.ok:
                    gate = {"ok": True, "passed_levels": gate.get("passed_levels")}
                # Learn from the build: fold the outcome into organism memory
                # so she gets stronger at building the more she builds.
                with suppress(Exception):
                    from remedy.core.build_persist import build_lesson_from_outcome
                    from remedy.memory.soul.update import record_self_inject_lesson

                    lesson = build_lesson_from_outcome(outcome, goal=g)
                    if lesson:
                        home = getattr(
                            getattr(runtime, "config", None), "home_dir", None
                        )
                        record_self_inject_lesson(
                            outcome=lesson["outcome"],
                            tree=lesson["tree"],
                            summary=lesson["summary"],
                            gate_detail=lesson["gate_detail"],
                            home=home,
                        )

    hops_ok = bool(hop_results) and all(r.get("ok") for r in hop_results if r.get("phase") != "scout")
    scout_only = bool(hop_results) and all(r.get("phase") == "scout" for r in hop_results)
    ok = bool(compiled.get("ok")) and bool((tdd.get("tdd") or {}).get("written"))
    if hop_results and not scout_only:
        ok = ok and hops_ok
    if gate:
        ok = ok and bool(gate.get("ok"))

    verify_status = "pending"
    if gate.get("ok"):
        verify_status = "completed"
    elif gate:
        verify_status = "in_progress"
    upsert_todos(
        runtime,
        [{"id": "verify", "content": "Verify green (gate tower / tests)", "status": verify_status}],
        merge=True,
    )

    review = review_write_set(runtime, write_set)
    review_fix: dict[str, Any] = {}
    with suppress(Exception):
        from remedy.core.build_review_fix import review_fix_pass

        review_fix = review_fix_pass(runtime, write_set, use_llm=bool(use_llm))
        if st is not None:
            st.review_fix_ran = True
    if st is not None:
        st.auto_drive_ran = True
        st.last_drive = {
            "ok": ok,
            "units": len(units),
            "hops": len(hop_results),
        }
        st.phase = "verify" if (tdd.get("tdd") or {}).get("written") else "implement"

    return {
        "ok": ok,
        "phase": "driven",
        "goal": g[:200],
        "use_llm": bool(use_llm),
        "compiled": {"ok": compiled.get("ok"), "units": len(units), "lock": compiled.get("lock")},
        "tdd": tdd.get("tdd") or {},
        "hops": hop_results,
        "gate": {k: gate.get(k) for k in ("ok", "message") if k in gate} if gate else {},
        "drive_to_green": drive_green,
        "repair": repair,
        "review": review,
        "review_fix": {
            "ok": review_fix.get("ok"),
            "errors": review_fix.get("errors"),
            "warns": review_fix.get("warns"),
            "message": review_fix.get("message"),
        }
        if review_fix
        else {},
        "message": _drive_summary(
            ok=ok,
            units=len(units),
            tdd=tdd,
            hops=hop_results,
            gate=gate,
            scout_only=scout_only,
        ),
    }


def _drive_summary(
    *,
    ok: bool,
    units: int,
    tdd: dict[str, Any],
    hops: list[dict[str, Any]],
    gate: dict[str, Any],
    scout_only: bool,
) -> str:
    written = (tdd.get("tdd") or {}).get("written") or []
    hop_ok = sum(1 for h in hops if h.get("ok"))
    hop_n = len(hops)
    gate_s = "n/a"
    if gate:
        gate_s = "green" if gate.get("ok") else "red"
    if scout_only:
        next_s = "file_write the unit bodies, then build_drive again or file_edit + verify."
    elif ok:
        next_s = "Machine loop green. Summarize and stop (or ship if required)."
    else:
        next_s = "Continue: file_edit failing units, then verify. Do not restart from scratch."
    return (
        f"build_drive {'OK' if ok else 'PARTIAL'} units={units} "
        f"tdd={len(written)} hops={hop_ok}/{hop_n} gate={gate_s}. {next_s}"
    )


def maybe_auto_implement(
    runtime: Any,
    state: Any,
    *,
    use_llm: bool | None = None,
) -> dict[str, Any] | None:
    """After explore thrash with zero writes: machine starts TDD + hops."""
    if state is None or not getattr(state, "active", False):
        return None
    try:
        from remedy.core.turn_context import current_plan_mode

        if current_plan_mode(runtime):
            return None
    except Exception:
        pass
    from remedy.core.build_delta import allow_background_drive

    # Live capable models write themselves. Auto TDD/hops steal the turn.
    if not allow_background_drive(state):
        return None
    if getattr(state, "auto_drive_ran", False):
        return None
    if int(getattr(state, "write_steps", 0) or 0) > 0:
        return None
    streak = int(getattr(state, "serial_explore_streak", 0) or 0)
    cap = int(getattr(state, "max_serial_explore", 3) or 3)
    forced = "force_implement" in (getattr(state, "nudges_emitted", None) or [])
    if getattr(state, "away_mode", False):
        forced = True
    if streak < cap and not forced:
        return None
    goal = str(getattr(state, "goal", "") or "")
    if re.search(r"(?i)\b(landing\s+page|web\s*page|html\s+page|index\.html)\b", goal):
        return None
    if not goal_wants_machine_implement(goal):
        return None
    if _project_root(runtime) is None:
        return None
    state.auto_drive_ran = True
    if use_llm is None:
        use_llm = should_use_live_llm(runtime)
    return drive_build(
        runtime,
        goal=str(getattr(state, "goal", "") or ""),
        use_llm=use_llm,
    )


def maybe_auto_repair(
    runtime: Any,
    state: Any,
    *,
    use_llm: bool | None = None,
    broaden: bool = False,
) -> dict[str, Any] | None:
    """On red verify: hop ranked repair targets (machine, not a prompt).

    ``broaden`` is the second, structurally-different strategy: more targets,
    more repairs per target, and test files included — used when the narrow
    source-first pass stalls.
    """
    if state is None or not getattr(state, "active", False):
        return None
    from remedy.core.build_delta import allow_background_drive

    # Isolated hops while the live model is repairing duplicate work.
    if not allow_background_drive(state):
        return None
    if getattr(state, "last_verify_ok", None) is not False:
        return None
    # Claim a cycle atomically: read-check-increment under a lock so two
    # concurrent entries can't both pass the cap and fire duplicate repair
    # hops that clobber each other's edits.
    cap = int(getattr(state, "max_auto_repair_cycles", 3) or 3)
    if broaden:
        cap = max(cap, cap + 3)
    with _auto_repair_lock:
        cycles = int(getattr(state, "auto_repair_cycles", 0) or 0)
        if cycles >= cap:
            return {
                "ok": False,
                "capped": True,
                "ran": 0,
                "message": f"auto-repair capped at {cap} cycles — model must file_edit",
            }
        state.auto_repair_cycles = cycles + 1
    from remedy.core.build_repair_queue import queue_from_error_vector, run_auto_repair_hops

    vec = getattr(state, "last_error_vector", None) or {}
    if not isinstance(vec, dict):
        vec = {}
    ws = list(getattr(state, "write_set", None) or [])
    root = _project_root(runtime)
    q = queue_from_error_vector(vec, write_set=ws, root=root)
    if not q.targets:
        # Nothing to repair — refund the cycle we optimistically claimed.
        with _auto_repair_lock:
            state.auto_repair_cycles = max(
                0, int(getattr(state, "auto_repair_cycles", 1) or 1) - 1
            )
        return None
    if use_llm is None:
        use_llm = should_use_live_llm(runtime)
    ran = run_auto_repair_hops(
        runtime,
        q,
        use_llm=bool(use_llm),
        max_targets=5 if broaden else 3,
        max_repairs=3 if broaden else 2,
        include_tests=broaden,
    )
    # Allow another auto-verify after hops landed
    if ran.get("ran"):
        state.auto_verify_ran = False
        state.phase = "repair"
    return {
        "ok": bool(ran.get("ok")),
        "capped": False,
        "ran": ran.get("ran"),
        "results": ran.get("results"),
        "targets": q.to_public(),
        "broaden": broaden,
        "message": (
            f"auto-repair hops ran={ran.get('ran')} ok={ran.get('ok')} "
            f"cycle={state.auto_repair_cycles}/{cap}"
            f"{' (broadened)' if broaden else ''}"
        ),
    }


def format_drive_message(result: dict[str, Any] | None) -> dict[str, str] | None:
    """User-role inject so the model continues from the machine hop, not from zero."""
    if not result:
        return None
    lines = [
        "[Build engine · MACHINE DRIVE]",
        str(result.get("message") or result.get("error") or "drive ran"),
    ]
    tdd = result.get("tdd") or {}
    written = tdd.get("written") or []
    if written:
        lines.append("TDD tests: " + ", ".join(str(w) for w in written[:8]))
    hops = result.get("hops") or []
    for h in hops[:8]:
        if not isinstance(h, dict):
            continue
        mark = "OK" if h.get("ok") else "RED"
        err = ""
        errs = h.get("errors") or ([h.get("error")] if h.get("error") else [])
        if errs:
            err = f" — {errs[0]}"[:160]
        lines.append(f"  hop [{mark}] {h.get('path') or h.get('symbol')}{err}")
    review = result.get("review") or {}
    if review.get("message"):
        lines.append(str(review["message"]))
    if result.get("capped"):
        lines.append("Repair cap hit. file_edit the failing unit; do not re-explore the tree.")
    elif not result.get("ok"):
        lines.append(
            "Continue from this state: file_edit / build_unit_hop the RED units, "
            "then verify. Do not rewrite the whole project."
        )
    return {"role": "user", "content": "\n".join(lines)}
