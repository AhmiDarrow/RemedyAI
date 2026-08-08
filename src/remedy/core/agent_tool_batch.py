"""Parallel tool batch execution for the ReAct loop.

Extracted from BasicRuntime so agent.py stays an orchestrator and batch
execution can be unit-tested without the full stream loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from uuid import uuid4

from remedy.core.errors import format_tool_error
from remedy.core.react_policy import (
    HARD_SAFETY_CHARS as _HARD_SAFETY_CHARS,
)
from remedy.core.react_policy import (
    MAX_PARALLEL_TOOLS as _MAX_PARALLEL_TOOLS,
)
from remedy.core.react_policy import (
    TOOL_RESULT_CHAR_CAP as _TOOL_RESULT_CHAR_CAP,
)
from remedy.core.react_policy import (
    _tool_call_fingerprint,
)
from remedy.core.react_stream import normalize_tool_calls
from remedy.models import ToolCall

logger = logging.getLogger(__name__)

# SSE/UI process-trace preview only (model payload uses TOOL_RESULT_CHAR_CAP / tier).
# Keep below typical L2/L3 model caps (12k) so fat dumps stay readable in Process Trace.
UI_TOOL_RESULT_PREVIEW_CHARS = 8_000


def progress_marker(
    *,
    label: str,
    step: int | None = None,
    total: int | None = None,
    percent: float | None = None,
    force_percent: bool = False,
) -> str:
    """Build @@progress payload for the desktop task progress bar.

    Single long jobs stay indeterminate (no percent) until finished so
    the UI does not freeze at 0%. Multi-step batches get real %.
    """
    payload: dict[str, Any] = {"label": label}
    if step is not None:
        payload["step"] = step
    if total is not None:
        payload["total"] = total
    multi = bool(total and total > 1)
    if percent is not None and (
        force_percent or multi or (step or 0) >= (total or 0) > 0
    ):
        payload["percent"] = round(float(percent), 1)
    elif multi and step is not None and total:
        payload["percent"] = round(100.0 * float(step) / float(total), 1)
    return f"@@progress:{json.dumps(payload, separators=(',', ':'))}"


_WRITE_TOOLS = frozenset({"file_write", "file_edit", "file_edit_batch"})


def _write_path_key(name: str, args: dict[str, Any]) -> str | None:
    """Lock key so concurrent writes never clobber the same path.

    Independent paths can run in parallel; intersecting paths share a key.
    ``file_edit_batch`` locks each path it touches (serialized via multi-lock
    in ``_run_one`` when multiple keys are needed — primary key is first path
    plus a global barrier only when batch lists multiple targets).
    """
    if name not in _WRITE_TOOLS:
        return None
    if name == "file_edit_batch":
        edits = args.get("edits") if isinstance(args, dict) else None
        paths: list[str] = []
        if isinstance(edits, list):
            for e in edits:
                if isinstance(e, dict) and e.get("path"):
                    paths.append(str(e.get("path") or "").strip().lower())
        if not paths and isinstance(args, dict) and args.get("path"):
            paths.append(str(args.get("path") or "").strip().lower())
        if not paths:
            return "__all_writes__"
        # Single stable key for the whole batch set (sorted join) so two
        # overlapping batches still serialize while disjoint path singles
        # use per-path keys below.
        if len(paths) > 1:
            return "batch:" + "|".join(sorted(set(paths)))
        return f"path:{paths[0]}"
    path = ""
    if isinstance(args, dict):
        path = str(args.get("path") or args.get("file") or "").strip().lower()
    if not path:
        return "__all_writes__"
    return f"path:{path}"


async def execute_tool_calls(runtime, tool_calls_list: list[dict[str, Any]],
        *,
        seen_fps: set[str],
        result_cache: dict[str, str],
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run tools in parallel (capped waves); always yield one tool msg per call id.

    Critical API contract: every ``tool_calls[].id`` on the preceding assistant
    message must receive a matching ``role=tool`` message. Cap and fingerprint
    dedupe may reduce *executions*, but never reduce *results*.

    Writes to the same path are serialized via per-path locks so parallel
    file_edit/file_write cannot clobber each other.
    """
    pending = normalize_tool_calls(tool_calls_list)
    if not pending:
        return

    # First occurrence of each fingerprint is the execution representative.
    fp_order: list[str] = []
    fp_to_tc: dict[str, dict[str, Any]] = {}
    for tc in pending:
        fp = _tool_call_fingerprint(tc)
        if fp not in fp_to_tc:
            fp_to_tc[fp] = tc
            fp_order.append(fp)

    path_locks: dict[str, asyncio.Lock] = {}

    async def _run_one(tc: dict[str, Any]) -> str:
        fn = tc.get("function") or {}
        name = (fn.get("name") or "").strip()
        raw_args = fn.get("arguments") or "{}"
        fp = _tool_call_fingerprint(tc)

        if fp in result_cache:
            return result_cache[fp]

        try:
            args = (
                json.loads(raw_args)
                if isinstance(raw_args, str)
                else dict(raw_args)
            )
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        # Refuse corrupted / history-summarized args before they hit the disk.
        # (Provider history stubs and stream-truncated JSON must never execute.)
        if isinstance(args, dict) and (
            args.get("_invalid_json")
            or args.get("_truncated")
            or args.get("_history_summarized")
        ):
            content_str = format_tool_error(
                "tool arguments were summarized or truncated for history — not real source",
                code="HISTORY_STUB",
                tool_name=name or "unknown",
                suggestion=(
                    "file_read the real path, then file_edit surgical hunks or "
                    "file_write with the full real source (not history stub text)."
                ),
            )
            result_cache[fp] = content_str
            seen_fps.add(fp)
            return content_str
        if name in _WRITE_TOOLS:
            blob = " ".join(
                str(args.get(k) or "")
                for k in ("content", "old_string", "new_string", "edits", "patch")
            )
            if any(
                m in blob
                for m in (
                    "history_stub kind=",
                    "DO_NOT_file_write_this_string",
                    "<<NOT_SOURCE_CODE",
                    "[file_write content omitted",
                    "omitted from provider history",
                )
            ):
                content_str = format_tool_error(
                    "refusing write: arguments look like a provider-history stub",
                    code="HISTORY_STUB",
                    tool_name=name or "unknown",
                    suggestion=(
                        "file_read the real path first; never re-write history stub text. "
                        "Use file_edit or a full real file_write body."
                    ),
                )
                result_cache[fp] = content_str
                seen_fps.add(fp)
                return content_str

        async def _call() -> tuple[Any, str, bool]:
            """Return (result, content, effective_ok).

            Soft failures (Error-prefixed / structured tool errors) still return
            data for the model, but count as failures for quality/metabolism so
            stuck recovery and fail streaks fire.
            """
            result = await runtime.call_tool(ToolCall(tool_name=name, arguments=args))
            if result.success:
                payload = result.data
                content_str = (
                    payload
                    if isinstance(payload, str)
                    else json.dumps(payload, default=str)
                )
            else:
                content_str = result.error or format_tool_error(
                    "tool failed",
                    code="TOOL_FAILED",
                    tool_name=name or "unknown",
                    suggestion="Retry with corrected arguments or a different tool.",
                )
            effective_ok = bool(result.success)
            if effective_ok:
                with suppress(Exception):
                    from remedy.core.react_policy import tool_content_is_error

                    if tool_content_is_error(content_str):
                        effective_ok = False
            return result, content_str, effective_ok

        # Shadow rehearsal (L2/L3 high-blast only) — never replaces write jail.
        # Resolve session quality once per tool (not per shadow/record path).
        shadow_outcome = "pass"
        sq_handle = None
        sid_tool = ""
        with suppress(Exception):
            from remedy.core.session_quality import get_session_quality
            from remedy.core.turn_context import turn_session_id

            sid_tool = str(turn_session_id(runtime) or "")
            sq_handle = get_session_quality(sid_tool)
        with suppress(Exception):
            from remedy.core.metabolism.shadow import rehearse, should_shadow
            from remedy.core.turn_context import turn_session_id

            tier = int(getattr(runtime, "_turn_tier", 2) or 2)
            strict = bool(getattr(runtime, "_shadow_strict", False))
            if not strict:
                with suppress(Exception):
                    from remedy.core.metabolism.governor import get_governor

                    strict = bool(
                        get_governor(sid_tool or str(turn_session_id(runtime) or "")).shadow_strict
                    )
            if should_shadow(name, tier=tier, strict=strict):
                roots = list(getattr(runtime, "_work_roots", None) or [])
                if not roots:
                    pp = getattr(runtime, "_project_path", None)
                    if pp:
                        roots = [str(pp)]
                map_hint: dict[str, Any] = {}
                with suppress(Exception):
                    from remedy.core.metabolism.machine_map import get_machine_map

                    sid_m = sid_tool or str(turn_session_id(runtime) or "")
                    sl = get_machine_map(sid_m).get("browser", "rail")
                    if sl is not None:
                        map_hint["browser_settled"] = bool(
                            (sl.data or {}).get("settled")
                        )
                sh = rehearse(
                    name,
                    args if isinstance(args, dict) else {},
                    tier=tier,
                    work_roots=roots,
                    map_hint=map_hint or None,
                    strict=strict,
                )
                shadow_outcome = sh.outcome
                if sh.blocked:
                    content_str = format_tool_error(
                        f"shadow rehearsal blocked: {sh.reason}",
                        code="SHADOW_BLOCK",
                        tool_name=name or "unknown",
                        suggestion=(
                            "Choose a safer path inside the project write roots, "
                            "or confirm intent if this was deliberate."
                        ),
                    )
                    result_cache[fp] = content_str
                    seen_fps.add(fp)
                    if sq_handle is not None:
                        with suppress(Exception):
                            sq_handle.record_shadow_catch()
                    # Still admit blocked attempt to evidence + IR (audit trail)
                    with suppress(Exception):
                        from remedy.core.metabolism.turn import after_tool_batch

                        after_tool_batch(
                            session_id=sid_tool or str(turn_session_id(runtime) or ""),
                            tool_name=name or "unknown",
                            arguments=args if isinstance(args, dict) else {},
                            content=content_str,
                            tool_call_id=str(
                                tc.get("id") or tc.get("tool_call_id") or ""
                            ),
                            success=False,
                            tier=tier,
                            action_ir=getattr(runtime, "_action_ir", None),
                            shadow_outcome=shadow_outcome,
                        )
                    return content_str
                if sh.outcome == "soft_warn" and sq_handle is not None:
                    with suppress(Exception):
                        sq_handle.record_shadow_catch()

        lock_key = _write_path_key(name, args)
        if lock_key:
            lock = path_locks.setdefault(lock_key, asyncio.Lock())
            async with lock:
                result, content_str, effective_ok = await _call()
        else:
            result, content_str, effective_ok = await _call()

        # Full tool results for the model (cap only if TOOL_RESULT_CHAR_CAP > 0).
        # Tier policy may further tighten (L0/L1 lean).
        cap = _TOOL_RESULT_CHAR_CAP if _TOOL_RESULT_CHAR_CAP > 0 else _HARD_SAFETY_CHARS
        with suppress(Exception):
            from remedy.core.metabolism.tier import tier_policy

            tpol = tier_policy(int(getattr(runtime, "_turn_tier", 2) or 2))
            if tpol.max_tool_result_chars and tpol.max_tool_result_chars < cap:
                cap = int(tpol.max_tool_result_chars)
        if len(content_str) > cap:
            content_str = (
                content_str[:cap]
                + f"\n…[safety cap {cap} chars — refine query if needed]"
            )
        if shadow_outcome == "soft_warn":
            content_str = (
                f"[shadow: soft_warn — double-check target]\n{content_str}"
            )
        result_cache[fp] = content_str
        seen_fps.add(fp)
        # Trace step for post-turn auto-learn (per-turn ContextVar list)
        with suppress(Exception):
            from remedy.core.turn_context import current_turn_tool_steps, turn_session_id

            steps = current_turn_tool_steps(runtime)
            if isinstance(steps, list):
                steps.append(
                    {
                        "tool": name or "unknown",
                        "args": {
                            k: (str(v)[:80] if not isinstance(v, (int, float, bool)) else v)
                            for k, v in list(args.items())[:12]
                        },
                        "success": bool(effective_ok),
                        "result": (content_str or "")[:200],
                        "error": None if effective_ok else (result.error or "failed"),
                        "duration_ms": float(getattr(result, "duration_ms", 0) or 0),
                    }
                )
        # Partner State Phase B: tool transaction ledger + write-set
        with suppress(Exception):
            from remedy.memory.partner_state import record_tool_from_runtime

            tc_id = str(tc.get("id") or tc.get("tool_call_id") or "")
            record_tool_from_runtime(
                runtime,
                name=name or "unknown",
                args=args if isinstance(args, dict) else {},
                result=content_str or "",
                success=bool(effective_ok),
                tool_call_id=tc_id,
            )
        # Metabolism: evidence ledger + decision currency + action IR
        with suppress(Exception):
            from remedy.core.metabolism.turn import after_tool_batch
            from remedy.core.turn_context import turn_session_id

            tc_id = str(tc.get("id") or tc.get("tool_call_id") or "")
            after_tool_batch(
                session_id=str(turn_session_id(runtime) or ""),
                tool_name=name or "unknown",
                arguments=args if isinstance(args, dict) else {},
                content=content_str or "",
                tool_call_id=tc_id,
                success=bool(effective_ok),
                tier=int(getattr(runtime, "_turn_tier", 2) or 2),
                action_ir=getattr(runtime, "_action_ir", None),
                shadow_outcome=shadow_outcome,
            )
        # Background continuity: pattern observation + stuck signals
        with suppress(Exception):
            from remedy.core.turn_context import turn_session_id
            from remedy.nanoswarm import get_swarm
            from remedy.nanoswarm.events import SwarmEvent

            sid = sid_tool or str(turn_session_id(runtime) or "")
            if sq_handle is not None:
                sq_handle.record_tool_result(success=bool(effective_ok))
            else:
                from remedy.core.session_quality import get_session_quality

                get_session_quality(sid).record_tool_result(
                    success=bool(effective_ok)
                )
            get_swarm().dispatch(
                SwarmEvent.tool_step(
                    name or "unknown",
                    success=bool(effective_ok),
                    duration_ms=float(getattr(result, "duration_ms", 0) or 0),
                    session_id=sid,
                )
            )
            # schedule_mid_turn_warm: once per wave (below), not per tool
        return content_str


    # Execute only fingerprints not already cached; never drop remainder past cap.
    to_run = [fp for fp in fp_order if fp not in result_cache]
    total_jobs = max(len(to_run), 1)
    completed_jobs = 0
    if to_run:
        first_name = (
            ((fp_to_tc[to_run[0]].get("function") or {}).get("name") or "tools").strip()
        )
        # Label only while in-flight; avoid a stuck 0% for one long job.
        yield progress_marker(
            label=first_name if len(to_run) == 1 else f"{len(to_run)} tools",
            step=0,
            total=total_jobs,
        ), {}

    # Frontier muscle (Grok/Claude/…) can fan out more tools per wave.
    parallel_cap = int(_MAX_PARALLEL_TOOLS)
    with suppress(Exception):
        runtime_cap = int(getattr(runtime, "_max_parallel_tools", 0) or 0)
        if runtime_cap > 0:
            parallel_cap = max(4, min(32, runtime_cap))
    for wave_start in range(0, len(to_run), parallel_cap):
        # Cooperative abort between waves (Stop should not wait for all tools).
        with suppress(Exception):
            from remedy.core.turn_context import is_turn_aborted

            if is_turn_aborted():
                for fp in to_run[wave_start:]:
                    if fp in result_cache:
                        continue
                    name = (
                        ((fp_to_tc[fp].get("function") or {}).get("name") or "").strip()
                    )
                    content_str = format_tool_error(
                        "turn aborted by user",
                        code="TURN_ABORTED",
                        tool_name=name or "unknown",
                        suggestion="Resend or continue when ready.",
                    )
                    result_cache[fp] = content_str
                    seen_fps.add(fp)
                break
        wave = to_run[wave_start : wave_start + parallel_cap]
        wave_names: list[str] = []
        for fp in wave:
            tc = fp_to_tc[fp]
            name = ((tc.get("function") or {}).get("name") or "").strip()
            wave_names.append(name or "tool")
            raw_args = (tc.get("function") or {}).get("arguments") or "{}"
            try:
                args_obj = (
                    json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                )
            except Exception:
                args_obj = {"_raw": str(raw_args)[:2000]}
            if not isinstance(args_obj, dict):
                args_obj = {"value": args_obj}
            # Structured tool_call for UI process trace — scrub secrets / large bodies
            call_id_ui = str(tc.get("id") or fp or name or "tool")
            ui_args = args_obj
            with suppress(Exception):
                from remedy.core.metabolism.redact import redact_obj

                ui_args = redact_obj(args_obj)
                if isinstance(ui_args, dict):
                    for heavy in ("content", "new_string", "old_string", "command", "text"):
                        if heavy in ui_args and isinstance(ui_args[heavy], str):
                            s = ui_args[heavy]
                            if len(s) > 200:
                                ui_args[heavy] = f"[omitted {len(s)} chars]"
            yield (
                "@@tool_call:"
                + json.dumps(
                    {
                        "name": name or "tool",
                        "args": ui_args,
                        "id": call_id_ui,
                        "call_id": call_id_ui,
                    },
                    default=str,
                    separators=(",", ":"),
                )
            ), {}
        label = (
            wave_names[0]
            if len(wave_names) == 1
            else f"{len(wave_names)} tools"
        )
        yield progress_marker(
            label=label,
            step=completed_jobs,
            total=total_jobs,
        ), {}

        results = await asyncio.gather(
            *[_run_one(fp_to_tc[fp]) for fp in wave],
            return_exceptions=True,
        )
        for fp, item in zip(wave, results, strict=True):
            name = ((fp_to_tc[fp].get("function") or {}).get("name") or "").strip()
            if isinstance(item, BaseException):
                logger.exception("parallel tool failed: %s", item)
                content_str = format_tool_error(
                    str(item),
                    code="TOOL_EXCEPTION",
                    tool_name=name or "unknown",
                    suggestion=(
                        "Retry with corrected arguments or a different tool "
                        "(list_dir / file_read)."
                    ),
                )
                result_cache[fp] = content_str
                seen_fps.add(fp)
                # Outer gather exceptions bypass _run_one telemetry — recover it.
                with suppress(Exception):
                    from remedy.core.metrics import default_registry

                    default_registry.counter(
                        "remedy_tool_batch_exceptions_total",
                        tool=name or "unknown",
                    ).inc()
                with suppress(Exception):
                    from remedy.core.session_quality import get_session_quality
                    from remedy.core.turn_context import turn_session_id

                    sid = str(turn_session_id(runtime) or "")
                    get_session_quality(sid).record_tool_result(success=False)
                with suppress(Exception):
                    from remedy.core.metabolism.turn import after_tool_batch
                    from remedy.core.turn_context import turn_session_id

                    tc_ex = fp_to_tc[fp]
                    after_tool_batch(
                        session_id=str(turn_session_id(runtime) or ""),
                        tool_name=name or "unknown",
                        arguments={},
                        content=content_str,
                        tool_call_id=str(
                            tc_ex.get("id") or tc_ex.get("tool_call_id") or ""
                        ),
                        success=False,
                        tier=int(getattr(runtime, "_turn_tier", 2) or 2),
                        action_ir=getattr(runtime, "_action_ir", None),
                        shadow_outcome="pass",
                    )
            # Success path already wrote result_cache inside _run_one.
            completed_jobs += 1
            done = completed_jobs >= total_jobs
            yield progress_marker(
                label=name or "tool",
                step=completed_jobs,
                total=total_jobs,
                percent=(100.0 if done else 100.0 * completed_jobs / total_jobs),
                force_percent=done,
            ), {}
        # Speculative warm once per wave (not per tool)
        with suppress(Exception):
            from remedy.core.agent_post_turn import schedule_mid_turn_warm

            schedule_mid_turn_warm(runtime)

    # Always emit one tool result per original tool_call id (API contract).
    for tc in pending:
        fp = _tool_call_fingerprint(tc)
        name = ((tc.get("function") or {}).get("name") or "").strip()
        content_str = result_cache.get(
            fp,
            format_tool_error(
                "tool produced no result",
                code="TOOL_EMPTY",
                tool_name=name or "unknown",
                suggestion="Retry the tool or answer from context.",
            ),
        )
        call_id = tc.get("id") or str(uuid4())
        # Surface generated images immediately (don't wait for model to restate).
        if name == "comfyui" and "@@REMEDY_IMAGE_MARKDOWN@@" in content_str:
            marker = "@@REMEDY_IMAGE_MARKDOWN@@"
            img_md = content_str.split(marker, 1)[-1].strip()
            if img_md:
                yield f"@@image_markdown:{img_md}", {}
            # Keep tool payload for the model without the huge data-URI blob.
            content_str = content_str.split(marker, 1)[0].strip()
            if len(content_str) > 2000:
                content_str = content_str[:2000] + "\n…[image already sent to user]"
        # UI process-trace preview (Full mode). Model still receives full content
        # below — only the SSE/UI surface is redacted + capped.
        # 12k is plenty for Process Trace; 500k blew up SSE/DOM on fat tool dumps.
        ui_trace_cap = UI_TOOL_RESULT_PREVIEW_CHARS
        preview = content_str
        with suppress(Exception):
            from remedy.core.metabolism.redact import redact_text

            preview = redact_text(preview)
        if len(preview) > ui_trace_cap:
            preview = (
                preview[:ui_trace_cap]
                + f"\n…[{len(content_str)} chars total — UI safety cap]"
            )
        ok = not (
            '"code": "TOOL_' in content_str
            or content_str.startswith("Error")
            or "TOOL_EXCEPTION" in content_str
            or "APPROVAL_REQUIRED" in content_str
            or "APPROVAL_CHECK_FAILED" in content_str
        )
        # Machine-native working memory: ingest the tool result so the middleman
        # can retrieve it later by query (without keeping the full body in context).
        with suppress(Exception):
            from remedy.core.turn_context import turn_session_id as _tsid
            from remedy.memory.middleman import ingest_tool_result

            ingest_tool_result(
                session_id=str(_tsid(runtime) or ""),
                content=content_str,
                tool=name or "",
            )
        yield (
            "@@tool_result:"
            + json.dumps(
                {
                    "name": name or "unknown",
                    "preview": preview,
                    "ok": ok,
                    "id": call_id,
                    "call_id": call_id,
                },
                default=str,
                separators=(",", ":"),
            )
        ), {
            "role": "tool",
            "tool_call_id": call_id,
            "content": content_str,
        }


