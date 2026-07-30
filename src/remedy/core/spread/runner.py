"""Run silent spread workers in parallel (mostly deterministic jobs)."""

from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any

from remedy.core.spread.types import SpreadResult, SpreadTask, WorkerResult

# Depth guard — workers must not call spread_run again.
_spread_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "remedy_spread_depth", default=0
)


def spread_depth() -> int:
    return int(_spread_depth.get() if _spread_depth is not None else 0)


async def run_spread(
    runtime: Any,
    tasks: list[SpreadTask],
    *,
    max_workers: int = 4,
    reason: str = "",
    strategy: str = "fanout",
) -> SpreadResult:
    """Execute tasks concurrently (capped waves); return merged digest."""
    t0 = time.perf_counter()
    max_workers = max(1, min(8, int(max_workers or 4)))
    if not tasks:
        return SpreadResult(
            ok=False,
            strategy="skipped",
            reason=reason or "no_tasks",
            wall_ms=0.0,
            max_workers=max_workers,
        )
    if spread_depth() >= 1:
        return SpreadResult(
            ok=False,
            strategy="skipped",
            reason="recursive_spread_blocked",
            tasks=tasks,
            wall_ms=0.0,
            max_workers=max_workers,
        )

    token = _spread_depth.set(spread_depth() + 1)
    results: list[WorkerResult] = []
    try:
        # Metrics
        try:
            from remedy.core.metrics import default_registry

            default_registry.counter("remedy_spread_runs_total").inc()
            default_registry.counter(
                "remedy_spread_workers_total"
            ).inc(len(tasks))
        except Exception:
            pass

        for wave_start in range(0, len(tasks), max_workers):
            wave = tasks[wave_start : wave_start + max_workers]
            wave_out = await asyncio.gather(
                *[_run_one(runtime, t) for t in wave],
                return_exceptions=True,
            )
            for t, out in zip(wave, wave_out, strict=False):
                if isinstance(out, WorkerResult):
                    results.append(out)
                elif isinstance(out, BaseException):
                    results.append(
                        WorkerResult(
                            id=t.id,
                            kind=t.kind,
                            ok=False,
                            summary=f"worker error: {out}",
                            model_used="none",
                        )
                    )
                else:
                    results.append(
                        WorkerResult(
                            id=t.id,
                            kind=t.kind,
                            ok=False,
                            summary="worker returned unknown type",
                        )
                    )
    finally:
        _spread_depth.reset(token)

    wall_ms = (time.perf_counter() - t0) * 1000.0
    merged = _merge_results(results, reason=reason)
    ok = any(r.ok for r in results)
    # Evidence ledger + decision currency for silent parallel work
    try:
        from remedy.core.metabolism.decision import get_decision_tracker
        from remedy.core.metabolism.evidence import get_evidence_ledger
        from remedy.core.turn_context import turn_session_id

        sid = str(turn_session_id(runtime) or getattr(runtime, "_session_id", "") or "")
        led = get_evidence_ledger(sid)
        admitted = led.admit_tool_result(
            tool_name="spread_run",
            content=merged or "",
            success=ok,
        )
        get_decision_tracker(sid).record_tool_batch(new_eu=len(admitted))
        get_decision_tracker(sid).record(
            "spread",
            f"spread ok={ok} workers={len(results)} wall_ms={wall_ms:.0f}",
        )
    except Exception:
        pass
    return SpreadResult(
        ok=ok,
        strategy=strategy,
        reason=reason or "fanout",
        tasks=tasks,
        results=results,
        merged_summary=merged,
        wall_ms=wall_ms,
        max_workers=max_workers,
    )


async def _run_one(runtime: Any, task: SpreadTask) -> WorkerResult:
    t0 = time.perf_counter()
    kind = (task.kind or "explore").strip().lower()
    try:
        if kind in ("explore", "read_map"):
            summary, ok, details = await _job(
                runtime, "explore", query=task.query or task.goal, path=task.path
            )
            model = "none"
        elif kind == "search":
            summary, ok, details = await _search_worker(runtime, task)
            model = "none"
        elif kind == "verify":
            summary, ok, details = await _job(
                runtime,
                "verify",
                command=task.command,
                path=task.path,
            )
            model = "none"
        elif kind == "diff":
            summary, ok, details = await _job(runtime, "diff", path=task.path)
            model = "none"
        elif kind == "review":
            summary, ok, details = await _review_worker(runtime, task)
            model = details.get("model_used") or "local"
        else:
            summary, ok, details = await _job(
                runtime, "explore", query=task.query, path=task.path
            )
            model = "none"
    except Exception as e:
        summary = f"worker failed: {e}"
        ok = False
        details = {}
        model = "none"

    # Never return secret-shaped tool dumps (keys in search hits / env files) unredacted.
    try:
        from remedy.core.metabolism.redact import redact_obj, redact_text

        summary = redact_text(str(summary or ""))
        if isinstance(details, dict):
            red_d = redact_obj(details)
            details = red_d if isinstance(red_d, dict) else {}
    except Exception:
        if any(
            tok in str(summary or "").lower()
            for tok in ("api_key", "sk-", "bearer ", "password=")
        ):
            summary = "[redacted worker summary]"
            details = {}

    cap = task.max_chars or 6_000
    if len(summary) > cap:
        summary = summary[:cap] + f"\n…[truncated {len(summary)}→{cap} chars]"

    return WorkerResult(
        id=task.id,
        kind=kind,
        ok=ok,
        summary=summary,
        details=details if isinstance(details, dict) else {},
        elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        model_used=str(model),
    )


async def _job(
    runtime: Any,
    kind: str,
    *,
    query: str = "",
    path: str = ".",
    command: str = "",
    timeout: float = 180.0,
) -> tuple[str, bool, dict[str, Any]]:
    from remedy.core.jobs import run_job

    try:
        result = await run_job(
            runtime,
            kind,
            query=query,
            path=path or ".",
            command=command,
            timeout=timeout,
        )
        return result.summary, bool(result.ok), dict(result.details or {})
    except Exception as e:
        # Path jail / resolve failures
        return f"error: {e}", False, {"error": str(e)}


async def _search_worker(
    runtime: Any, task: SpreadTask
) -> tuple[str, bool, dict[str, Any]]:
    from remedy.core.repo_search import format_hits, search_repo

    root = runtime.effective_project_path()
    path = (task.path or ".").strip() or "."
    query = (task.query or task.goal or "").strip()
    if not query:
        return "error: search query empty", False, {}
    try:
        resolved = runtime.resolve_tool_path(path)
        search_path = str(resolved)
    except Exception as e:
        return f"error: path not allowed: {e}", False, {}
    home = getattr(getattr(runtime, "config", None), "home_dir", None)
    roots = None
    scope = "project"
    try:
        roots = runtime.allowed_roots()
        scope = runtime.access_scope()
    except Exception:
        pass
    hits, engine = search_repo(
        root,
        query,
        path=search_path,
        max_matches=40,
        home_dir=home,
        allowed_roots=roots,
        access_scope=scope,
    )
    body = format_hits(hits, engine=engine, pattern=query)
    return body, True, {"engine": engine, "hits": len(hits)}


async def _review_worker(
    runtime: Any, task: SpreadTask
) -> tuple[str, bool, dict[str, Any]]:
    """Cheap review: explore + optional local summarize (no frontier by default)."""
    summary, ok, details = await _job(
        runtime, "explore", query=task.query or task.goal, path=task.path
    )
    model_used = "none"
    if ok and len(summary) > 2500:
        compressed = _local_summarize(
            summary,
            goal=task.goal or task.query or "Summarize findings",
        )
        if compressed:
            summary = compressed
            model_used = "local"
            details = {**details, "model_used": "local"}
    return summary, ok, {**details, "model_used": model_used}


def _local_summarize(text: str, *, goal: str) -> str | None:
    try:
        from remedy.vision.runtime import is_running

        if not is_running():
            return None
    except Exception:
        return None
    try:
        from remedy.runtime.jobs import LocalJob, LocalRole, default_queue
        from remedy.runtime.local_infer import ensure_handlers_registered
        from remedy.vision.config import load_vision_json

        ensure_handlers_registered()
        cfg = load_vision_json()
        base_url = str(cfg.get("base_url") or "") or "http://127.0.0.1:8742"
        prompt = (
            f"Goal: {goal[:200]}\n"
            "Summarize the worker findings in ≤12 bullet lines. No preamble.\n\n"
            f"{text[:6000]}"
        )
        q = default_queue()
        job = LocalJob(
            role=LocalRole.NANO,
            kind="worker_summarize",
            payload={
                "prompt": prompt,
                "base_url": base_url,
                "max_tokens": 280,
                "timeout_s": 20.0,
                "system": "You compress scout findings for a coding agent parent. Bullets only.",
            },
            priority=0,
        )
        out = q.submit(job, wait=True, timeout=25.0)
        if not out.get("ok"):
            return None
        raw = out.get("result") or {}
        s = (
            str(raw.get("text") or "").strip()
            if isinstance(raw, dict)
            else str(raw).strip()
        )
        return s or None
    except Exception:
        return None


def _merge_results(results: list[WorkerResult], *, reason: str) -> str:
    lines = [
        f"[spread {len(results)} workers — {reason or 'fanout'}]",
        "WORKER PREAMBLE results (parent synthesizes; do not re-run the same scouts):",
        "",
    ]
    for r in results:
        status = "ok" if r.ok else "fail"
        lines.append(
            f"## [{r.id}] {r.kind} ({status}, {r.elapsed_ms:.0f}ms, model={r.model_used})"
        )
        lines.append(r.summary.strip() or "(empty)")
        lines.append("")
    return "\n".join(lines).strip()[:48_000]
