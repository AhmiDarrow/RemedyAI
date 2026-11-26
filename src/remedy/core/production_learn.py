"""Production-path learning hooks used by the Go turn runner via RMDY.

``prompt.assemble`` already builds the system prompt; these helpers attach the
organism pulse at turn start (called from assemble) and run post-turn memory,
skill-genome, and LearningLoop work at turn end without blocking the reply.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LEARN_LOG_NAME = "learn_log.jsonl"
LEARN_LOG_MAX_BYTES = 256 * 1024
LEARN_LOG_MAX_LINES = 400

# Frontier / workspace ABI names → the metabolism ledger's historical names.
_ABI_TO_METABOLISM = {
    "read": "file_read",
    "workspace.read": "file_read",
    "write": "file_write",
    "workspace.write": "file_write",
    "edit": "file_edit",
    "workspace.edit": "file_edit",
    "bash": "bash_exec",
    "shell.exec": "bash_exec",
    "computer.navigate": "computer_navigate",
    "computer.screenshot": "computer_screenshot",
    "computer.snapshot": "computer_snapshot",
    "computer.act": "computer_act",
    "computer.windows": "computer_windows",
}


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




def learn_log_path(home: str | None) -> Path | None:
    raw = str(home or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser() / LEARN_LOG_NAME


def record_learn_run(
    *,
    kind: str,
    home: str | None,
    duration_ms: float = 0.0,
    timeout: bool = False,
    error: str = "",
    changed: Mapping[str, Any] | None = None,
    ok: bool = True,
    source: str = "python",
) -> None:
    """Append one background-learning line and cap the file. Never raises."""
    path = learn_log_path(home)
    if path is None or not str(kind or "").strip():
        return
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time() % 1) * 1_000_000):06d}Z",
        "kind": str(kind).strip(),
        "duration_ms": round(float(duration_ms or 0.0), 3),
        "ok": bool(ok) and not timeout and not error,
        "source": source,
    }
    if timeout:
        entry["timeout"] = True
        entry["ok"] = False
    if error:
        entry["error"] = str(error)[:240]
        entry["ok"] = False
    if changed:
        clean = {k: v for k, v in dict(changed).items() if v not in (None, "", [], {})}
        if clean:
            entry["changed"] = clean
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _rotate_learn_log(path)
    except Exception:
        logger.debug("record_learn_run failed", exc_info=True)


def recent_learn_runs(home: str | None, limit: int = 20) -> list[dict[str, Any]]:
    """Return the newest learn-log rows (oldest first), for status / diagnostics."""
    path = learn_log_path(home)
    if path is None or not path.is_file():
        return []
    if limit <= 0:
        limit = 20
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        with suppress(Exception):
            item = json.loads(raw)
            if isinstance(item, dict):
                rows.append(item)
    return rows[-limit:]


def _learn_log_caps() -> tuple[int, int]:
    try:
        from remedy.core.organism_knobs import resolve_int

        return resolve_int("learn_log_max_bytes"), resolve_int("learn_log_max_lines")
    except Exception:
        return LEARN_LOG_MAX_BYTES, LEARN_LOG_MAX_LINES


def _rotate_learn_log(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    max_bytes, max_lines = _learn_log_caps()
    if size <= max_bytes:
        return
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return
    if len(lines) <= max_lines // 2:
        return
    keep = lines[-max_lines:] if len(lines) > max_lines else lines[len(lines) // 2 :]
    tmp: Path | None = None
    try:
        from remedy.core.atomic_json import replace_with_retry, scratch_path

        tmp = scratch_path(path)
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        replace_with_retry(tmp, path)
    except Exception:
        with suppress(Exception):
            if tmp is not None:
                tmp.unlink(missing_ok=True)


def _changed_from(out: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "residue",
        "genome",
        "learned",
        "skill",
        "eu_new",
        "evidence_units",
        "tools",
        "organism",
        "code",
        "skipped",
        "idle_ready",
    )
    changed: dict[str, Any] = {}
    for key in keys:
        if key not in out:
            continue
        val = out[key]
        if val in (None, "", 0, False, [], {}):
            continue
        if key == "organism" and isinstance(val, dict) and not any(val.values()):
            continue
        changed[key] = val
    return changed


def _with_learn_log(kind: str, home: str | None, fn):
    started = time.perf_counter()
    error = ""
    out: dict[str, Any] = {}
    try:
        result = fn()
        out = result if isinstance(result, dict) else {"ok": True, "result": result}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:240]
        logger.debug("%s failed", kind, exc_info=True)
        out = {"ok": False, "error": error, "skipped": "error"}
    duration_ms = (time.perf_counter() - started) * 1000.0
    record_learn_run(
        kind=kind,
        home=home,
        duration_ms=duration_ms,
        error=error,
        changed=_changed_from(out),
        ok=bool(out.get("ok", True)) and not error,
    )
    out.setdefault("duration_ms", round(duration_ms, 3))
    return out


def metabolism_tool_name(name: str) -> str:
    raw = (name or "").strip()
    return _ABI_TO_METABOLISM.get(raw, raw)


def admit_production_tool_batch(inp: Mapping[str, Any]) -> dict[str, Any]:
    """Admit one mid-turn tool batch into metabolism. Never raises."""

    def _run() -> dict[str, Any]:
        from remedy.core.metabolism.turn import after_tool_batch

        session_id = str(inp.get("session_id") or "").strip()
        try:
            tier = int(inp.get("tier") or 2)
        except (TypeError, ValueError):
            tier = 2
        raw = inp.get("results") or inp.get("tool_steps") or inp.get("last_results") or []
        if not isinstance(raw, list):
            raw = []
        totals: dict[str, Any] = {"ok": True, "eu_new": 0, "evidence_units": 0, "tools": 0}
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = metabolism_tool_name(
                str(item.get("tool") or item.get("name") or item.get("tool_name") or "")
            )
            if not name:
                continue
            args = item.get("arguments") or item.get("input") or {}
            if not isinstance(args, dict):
                args = {}
            content = str(item.get("result") or item.get("content") or item.get("tail") or item.get("output") or "")
            error = str(item.get("error") or "")
            ok = item.get("ok", item.get("success", error == ""))
            try:
                success = bool(ok) and not error
            except Exception:
                success = not error
            admitted = after_tool_batch(
                session_id=session_id,
                tool_name=name,
                arguments=args,
                content=content,
                tool_call_id=str(item.get("tool_call_id") or item.get("id") or ""),
                success=success,
                tier=tier,
            )
            totals["eu_new"] = int(totals["eu_new"]) + int(admitted.get("eu_new") or 0)
            totals["evidence_units"] = int(admitted.get("evidence_units") or totals["evidence_units"])
            totals["tools"] = int(totals["tools"]) + 1
        return totals

    try:
        return _with_learn_log("after_tool_batch", _home_of(inp), _run)
    except Exception:
        logger.debug("admit_production_tool_batch failed", exc_info=True)
        return {"ok": True, "skipped": "error", "eu_new": 0, "evidence_units": 0, "tools": 0}

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
    return _with_learn_log("turn_end", _home_of(inp), lambda: _finish_production_turn_inner(inp))


def _finish_production_turn_inner(inp: Mapping[str, Any]) -> dict[str, Any]:
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
            if session_id:
                # Post-turn work reads the session through turn_session_id,
                # which falls back to the runtime outside a Python turn.
                with suppress(Exception):
                    runtime._session_id = session_id

        with suppress(Exception):
            from remedy.core.agent_post_turn import schedule_post_turn_prep

            schedule_post_turn_prep(
                runtime,
                message=user_text,
                session_id=session_id or None,
                soul_native=bool(inp.get("soul_native")),
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

        if home:
            with suppress(Exception):
                from remedy.core.metabolism.alive import note_turn_outcome

                noted = note_turn_outcome(
                    home,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    steps=steps,
                )
                if noted.get("kind"):
                    out["outcome"] = noted["kind"]
    except Exception:
        logger.debug("finish_production_turn failed", exc_info=True)
        return {"ok": True, "skipped": "error", "residue": "", "genome": 0, "learned": False}
    return out


def run_unattended_learn(inp: Mapping[str, Any]) -> dict[str, Any]:
    """One hearth learning tick: organism cycle, optional idle self-heal.

    Code self-inject stays opt-in and idle-gated inside run_unattended_improve.
    Never raises to the caller; never calls a provider.
    """
    return _with_learn_log("improve", _home_of(inp), lambda: _run_unattended_learn_inner(inp))


def _run_unattended_learn_inner(inp: Mapping[str, Any]) -> dict[str, Any]:
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
            out["compact"] = result.get("compact") or result.get("organism", {}).get("compact")
            org = out["organism"] if isinstance(out["organism"], dict) else {}
            compacted = bool(
                (isinstance(out.get("compact"), dict) and out["compact"].get("changed"))
                or org.get("compacted")
                or org.get("dreamed")
                or org.get("did")
                or org.get("recalled")
                or result.get("code")
            )
            out["changed"] = compacted
    except Exception:
        logger.debug("run_unattended_learn failed", exc_info=True)
        out["skipped"] = "error"
    return out
