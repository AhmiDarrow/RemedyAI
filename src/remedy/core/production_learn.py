"""Production-path learning hooks used by the Go turn runner via RMDY.

``prompt.assemble`` already builds the system prompt; these helpers attach the
organism pulse at turn start (called from assemble) and run post-turn memory,
skill-genome, and LearningLoop work at turn end without blocking the reply.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def _home_of(inp: Mapping[str, Any], runtime: Any = None) -> str | None:
    home = str(inp.get("home_dir") or "").strip()
    if home:
        return home
    with suppress(Exception):
        home = str(getattr(getattr(runtime, "config", None), "home_dir", None) or "").strip()
        if home:
            return home
    with suppress(Exception):
        import os

        home = str(os.environ.get("REMEDY_HOME") or "").strip()
        if home:
            return home
    return None


def _runtime_for(inp: Mapping[str, Any]) -> Any | None:
    """Reuse the cached assemble runtime so soul/memory stay on one home."""
    with suppress(Exception):
        from remedy.runtime.prompt_assemble import runtime_for_learn

        return runtime_for_learn(inp)
    return None


def _normalize_steps(raw: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return steps
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("tool")
            or item.get("name")
            or item.get("tool_name")
            or ""
        ).strip()
        if not name:
            continue
        result = str(item.get("result") or item.get("tail") or item.get("output") or "")
        error = str(item.get("error") or "")
        ok = item.get("ok", item.get("success", error == ""))
        try:
            success = bool(ok) and not error
        except Exception:
            success = not error
        step: dict[str, Any] = {
            "tool": name,
            "tool_name": name,
            "name": name,
            "success": success,
            "ok": success,
            "result": result[:500],
            "error": error[:240] if error else None,
        }
        with suppress(Exception):
            step["duration_ms"] = float(item.get("duration_ms") or 0.0)
        steps.append(step)
    return steps


def finish_production_turn(inp: Mapping[str, Any]) -> dict[str, Any]:
    """Post-turn learning for a Go cognition turn. Never raises to the caller."""
    out: dict[str, Any] = {"ok": True, "residue": "", "genome": 0, "learned": False}
    try:
        runtime = _runtime_for(inp)
        home = _home_of(inp, runtime)
        session_id = str(inp.get("session_id") or "").strip()
        user_text = str(inp.get("message") or inp.get("user_text") or inp.get("goal") or "")
        assistant_text = str(inp.get("assistant_text") or inp.get("text") or "")
        steps = _normalize_steps(inp.get("tool_steps") or inp.get("last_results") or [])

        if runtime is not None:
            with suppress(Exception):
                runtime._last_assistant_text = assistant_text
            with suppress(Exception):
                runtime._turn_tool_steps = list(steps)
            with suppress(Exception):
                from remedy.core.turn_context import set_turn_session_id

                if session_id:
                    set_turn_session_id(session_id, runtime)

        with suppress(Exception):
            from remedy.core.agent_post_turn import schedule_post_turn_prep

            schedule_post_turn_prep(
                runtime,
                message=user_text,
                session_id=session_id or None,
            )

        # schedule_post_turn_prep no-ops without a runtime; still ingest residue.
        if home and (user_text or assistant_text):
            with suppress(Exception):
                from remedy.core.metabolism.turn import end_turn_metabolism

                end = end_turn_metabolism(
                    session_id=session_id or "",
                    assistant_text=assistant_text,
                    user_text=user_text,
                    recent_tool_texts=[
                        f"{s.get('tool')}: {s.get('result') or s.get('error') or ''}"[:500]
                        for s in steps[-12:]
                    ],
                    allow_verify=len(steps) >= 2,
                    home=home,
                )
                if end.get("cas_residue"):
                    out["residue"] = str(end["cas_residue"])

        if steps:
            with suppress(Exception):
                from remedy.core.metabolism.skill_genome import get_skill_genome

                genome = get_skill_genome()
                recorded = 0
                for step in steps:
                    name = str(step.get("tool") or "")
                    # Phenotype ranks skill procedures; also record tool-named skills.
                    if name.startswith("skill") or name.startswith("skill."):
                        sid = str(step.get("result") or name)
                        genome.record(sid[:80] or name, ok=bool(step.get("ok")), latency_ms=float(step.get("duration_ms") or 0))
                        recorded += 1
                    elif "skill" in name.lower():
                        genome.record(name, ok=bool(step.get("ok")), latency_ms=float(step.get("duration_ms") or 0))
                        recorded += 1
                # Always persist a turn-level signal when skill tools ran; also
                # record a stable id for multi-tool work so tests and ranking
                # have a phenotype file after a real Go-path turn.
                if recorded == 0 and len(steps) >= 2:
                    first = str(steps[0].get("tool") or "turn")
                    genome.record(first, ok=all(bool(s.get("ok")) for s in steps), latency_ms=0.0)
                    recorded = 1
                if home:
                    genome.persist(home)
                out["genome"] = recorded

        # Pregate reads the session pattern buffer, not the steps list. Without
        # this, Go-path turns never pass the LearningLoop gate.
        if steps:
            with suppress(Exception):
                from remedy.nanoswarm import get_swarm

                pattern = get_swarm().pattern
                for step in steps:
                    pattern.on_tool_step(
                        tool_name=str(step.get("tool") or ""),
                        success=bool(step.get("ok")),
                        duration_ms=float(step.get("duration_ms") or 0.0),
                        session_id=session_id or None,
                    )

        with suppress(Exception):
            from pathlib import Path

            from remedy.core.agent_learn import auto_learn_from_turn
            from remedy.core.learning_loop import LearningLoop

            ll = None
            getter = getattr(runtime, "_get_learning_loop", None) if runtime is not None else None
            ll = getter() if callable(getter) else None
            if ll is None and home:
                ll = LearningLoop(skills_dir=Path(home).expanduser() / "skills")
            skill = auto_learn_from_turn(
                learning_loop=ll,
                message=user_text,
                session_id=session_id or None,
                steps=steps,
                allow_creation=True,
            )
            if skill is not None:
                out["learned"] = True
                out["skill"] = str(getattr(getattr(skill, "manifest", None), "name", "") or "")
    except Exception:
        logger.debug("finish_production_turn failed", exc_info=True)
        return {"ok": True, "skipped": "error", "residue": "", "genome": 0, "learned": False}
    return out


def run_unattended_learn(inp: Mapping[str, Any]) -> dict[str, Any]:
    """One hearth learning tick: organism cycle, optional idle self-heal.

    Code self-inject stays opt-in and idle-gated inside run_unattended_improve.
    Never raises to the caller; never calls a provider.
    """
    out: dict[str, Any] = {"ok": True, "organism": {}, "code": None}
    try:
        import asyncio

        from remedy.core.self_inject import run_unattended_improve

        runtime = _runtime_for(inp)
        home = _home_of(inp, runtime)

        async def _go() -> dict[str, Any]:
            return await run_unattended_improve(runtime=runtime, home=home)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, _go()).result(timeout=45)
        else:
            result = asyncio.run(_go())
        if isinstance(result, dict):
            out["organism"] = result.get("organism") or {}
            out["code"] = result.get("code")
            out["idle_s"] = result.get("idle_s")
            out["idle_ready"] = result.get("idle_ready")
    except Exception:
        logger.debug("run_unattended_learn failed", exc_info=True)
        out["skipped"] = "error"
    return out
