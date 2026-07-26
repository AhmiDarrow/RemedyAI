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


async def execute_tool_calls(runtime, tool_calls_list: list[dict[str, Any]],
        *,
        seen_fps: set[str],
        result_cache: dict[str, str],
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run tools in parallel (capped waves); always yield one tool msg per call id.

    Critical API contract: every ``tool_calls[].id`` on the preceding assistant
    message must receive a matching ``role=tool`` message. Cap and fingerprint
    dedupe may reduce *executions*, but never reduce *results*.
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
        # Full tool results for the model (cap only if TOOL_RESULT_CHAR_CAP > 0).
        cap = _TOOL_RESULT_CHAR_CAP if _TOOL_RESULT_CHAR_CAP > 0 else _HARD_SAFETY_CHARS
        if len(content_str) > cap:
            content_str = (
                content_str[:cap]
                + f"\n…[safety cap {cap} chars — re-run with a narrower query if needed]"
            )
        result_cache[fp] = content_str
        seen_fps.add(fp)
        # Trace step for post-turn auto-learn
        with suppress(Exception):
            steps = getattr(runtime, "_turn_tool_steps", None)
            if isinstance(steps, list):
                steps.append(
                    {
                        "tool": name or "unknown",
                        "args": {
                            k: (str(v)[:80] if not isinstance(v, (int, float, bool)) else v)
                            for k, v in list(args.items())[:12]
                        },
                        "success": bool(result.success),
                        "result": (content_str or "")[:200],
                        "error": None if result.success else (result.error or "failed"),
                        "duration_ms": float(getattr(result, "duration_ms", 0) or 0),
                    }
                )
        # Background continuity: pattern observation + stuck signals
        with suppress(Exception):
            from remedy.core.agent_post_turn import schedule_mid_turn_warm
            from remedy.core.session_quality import get_session_quality
            from remedy.nanoswarm import get_swarm
            from remedy.nanoswarm.events import SwarmEvent

            get_session_quality(
                str(getattr(runtime, "_session_id", "") or "")
            ).record_tool_result(success=bool(result.success))
            get_swarm().dispatch(
                SwarmEvent.tool_step(
                    name or "unknown",
                    success=bool(result.success),
                    duration_ms=float(getattr(result, "duration_ms", 0) or 0),
                    session_id=str(getattr(runtime, "_session_id", "") or ""),
                )
            )
            # Speculative prep while more tools / model continue
            schedule_mid_turn_warm(runtime)
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

    for wave_start in range(0, len(to_run), _MAX_PARALLEL_TOOLS):
        wave = to_run[wave_start : wave_start + _MAX_PARALLEL_TOOLS]
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
            # Structured tool_call for UI process trace (args for full mode).
            call_id_ui = str(tc.get("id") or fp or name or "tool")
            yield (
                "@@tool_call:"
                + json.dumps(
                    {
                        "name": name or "tool",
                        "args": args_obj,
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
        # Full raw dump for UI process trace (Full mode).
        # Keep a hard ceiling only so multi‑MB binary dumps cannot freeze SSE.
        ui_trace_cap = 500_000
        preview = content_str
        if len(preview) > ui_trace_cap:
            preview = (
                preview[:ui_trace_cap]
                + f"\n…[{len(content_str)} chars total — UI safety cap]"
            )
        ok = not (
            '"code": "TOOL_' in content_str
            or content_str.startswith("Error")
            or "TOOL_EXCEPTION" in content_str
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


