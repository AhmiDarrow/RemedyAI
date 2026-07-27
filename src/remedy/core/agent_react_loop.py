"""ReAct LLM stream loop.

Extracted from BasicRuntime._call_llm_stream so agent.py remains a thin
orchestrator. Takes a runtime instance for config, tools, and side effects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import aiohttp

from remedy.core.agent_tool_batch import execute_tool_calls
from remedy.core.react_policy import (
    TOOL_RESULT_CHAR_CAP as _TOOL_RESULT_CHAR_CAP,
)
from remedy.core.react_policy import (
    _looks_like_pseudo_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    batch_has_approval_required,
    batch_has_empty_search,
    batch_has_tool_errors,
    epoch_continue_message,
    is_productive_tool_batch,
    looks_like_false_progress,
    mission_verify_gate_message,
    recovery_nudge_message,
    strip_tool_markup,
    turn_has_unfinished_work,
)
from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_sse_chunk,
    build_assistant_api_message,
    build_runtime_system_block,
    ensure_tool_call_pairings,
    filter_fresh_tool_calls,
    finalize_round_text,
    normalize_tool_calls,
    parse_sse_data_line,
    repair_reasoning_content_in_messages,
    should_enable_tools,
)

logger = logging.getLogger(__name__)


async def call_llm_stream(runtime, message: str,
        session_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        *,
        plan_mode: bool = False,
    ) -> AsyncIterator[str]:
    """Call the LLM with a smooth multi-epoch ReAct loop.

    Yields status tokens prefixed with '@@' for tool-call lifecycle events.
    Soft epoch walls compact context and checkpoint — they do **not** strip
    tools while work is unfinished. Only the absolute safety ceiling (or a
    no-progress stale-epoch stop) forces a final answer.

    When *plan_mode* is True, only planning tools run (no shell/file writes).
    """
    try:
        from remedy.interfaces.attachments import build_multimodal_user_content

        # For Partner Memory ranking + quiet distillation hooks
        with suppress(Exception):
            runtime._last_user_text = (message or "")[:4000]
        context = await runtime._build_context()
        # Surface active plan + plan-mode instructions
        with suppress(Exception):
            from pathlib import Path

            from remedy.core.plan_store import PlanStore

            home = getattr(runtime.config, "home_dir", None) or (Path.home() / ".remedy")
            store = PlanStore(home)
            plan = store.latest_for_session(session_id)
            if plan is not None:
                context = (
                    (context or "")
                    + "\n\n## Active task plan\n"
                    + plan.summary_markdown()
                )
            if plan_mode:
                from remedy.core.plan_store import PLAN_MODE_SYSTEM_ADDENDUM

                context = (context or "") + "\n\n" + PLAN_MODE_SYSTEM_ADDENDUM
        history = await runtime._load_session_history(session_id, message)
        # Memory Harness L0: prune send-view only (stored transcript untouched)
        with suppress(Exception):
            from remedy.memory.harness.pruner import prune_messages_for_send

            if runtime._harness_mode != "off":
                # max_tool_chars=0 → no content shortening (dedupe only).
                history = prune_messages_for_send(
                    history,
                    max_tool_chars=_TOOL_RESULT_CHAR_CAP,
                    dedupe_tools=True,
                )
        # Visual decoder path for text-only chat models + image attachments.
        vision_mode = "native"
        decode_brief: str | None = None
        with suppress(Exception):
            from remedy.vision.service import decode_for_turn

            cfg_for_vision: dict[str, Any] = {}
            with suppress(Exception):
                from remedy.interfaces.config import load_config

                cfg_for_vision = load_config() or {}
            vres = decode_for_turn(
                attachments,
                provider=runtime._llm_provider,
                model=runtime._llm_model,
                cfg=cfg_for_vision,
            )
            mode = str(vres.get("mode") or "native")
            if mode == "decode" and vres.get("combined"):
                vision_mode = "decode"
                decode_brief = str(vres.get("combined") or "")
                for ev in vres.get("events") or []:
                    yield f"@@status:{ev}\n"
            elif mode == "unavailable" and vres.get("hint"):
                # Inject hint text (no image_url) so text-only models stay safe
                vision_mode = "decode"
                decode_brief = (
                    f"[Visual decoder unavailable] {vres.get('hint')}\n"
                    "Image files are attached by path only."
                )
                yield (
                    "@@status:Visual decoder unavailable — "
                    "enable in Settings for local image understanding\n"
                )

        user_content = build_multimodal_user_content(
            message,
            attachments,
            vision_mode=vision_mode,
            decode_brief=decode_brief,
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_runtime_system_block(
                    system_prompt=runtime._system_prompt,
                    provider=runtime._llm_provider,
                    model=runtime._llm_model,
                    base_url=runtime._llm_base_url,
                    max_steps=runtime._max_react_steps,
                    context=context,
                ),
            },
            *history,
            {"role": "user", "content": user_content},
        ]
        # Continuity / Memory Harness v2: enforce lean send-view + optional local brief
        with suppress(Exception):
            if runtime._harness_mode == "auto":
                from remedy.memory.harness.send_policy import (
                    apply_auto_harness_send_policy,
                )

                sid = str(getattr(runtime, "_session_id", "") or session_id or "")
                messages, _hmeta = apply_auto_harness_send_policy(
                    runtime,
                    messages,
                    user_text=message or "",
                    session_id=sid,
                    tool_result_char_cap=int(_TOOL_RESULT_CHAR_CAP or 0),
                )
                # Soft library skill tip for desktop chip (cache-ranked; not an install)
                with suppress(Exception):
                    snap = getattr(runtime, "_last_context_snapshot", None)
                    lib = (getattr(snap, "signals", None) or {}).get("library_suggest")
                    if isinstance(lib, dict) and lib.get("id"):
                        import json as _json

                        yield (
                            "@@library_suggest:"
                            + _json.dumps(lib, default=str, separators=(",", ":"))
                            + "\n"
                        )
        all_tools = runtime._openai_tools()
        if plan_mode:
            from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

            tools = [
                t
                for t in all_tools
                if ((t.get("function") or {}).get("name") or "") in PLAN_MODE_TOOL_NAMES
            ]
        else:
            # Creative image prompts ("make something cool/spacey") must keep tools on.
            # Also: unfinished multi-turn work (history tool use / open tasks) so short
            # follow-ups like "go with your suggestions" keep agency.
            open_tasks: list[str] = []
            with suppress(Exception):
                brief = getattr(runtime, "_session_brief", None)
                if brief is not None:
                    open_tasks = list(getattr(brief, "open_tasks", None) or [])
            tools = (
                all_tools
                if should_enable_tools(
                    message,
                    all_tools,
                    has_attachments=bool(attachments),
                    history=history,
                    open_tasks=open_tasks or None,
                )
                or bool(
                    re.search(
                        r"\b(comfy|image|picture|nebula|spacey|generate|draw|illustrat|logo|asset|png)\b",
                        message or "",
                        re.I,
                    )
                )
                else []
            )

        seen_fps: set[str] = set()
        result_cache: dict[str, str] = {}
        produced_user_text = False
        pseudo_recovery_done = False
        pseudo_nudge_count = 0
        # Nudge once when the model claims progress without native tool_calls.
        false_progress_nudge_count = 0
        # One automatic recovery nudge per turn after a failing tool batch.
        recovery_nudge_done = False
        headers = runtime._provider.auth_headers(runtime._llm_api_key)
        endpoint = runtime._provider.chat_endpoint(runtime._llm_base_url)

        # Long agent runs: high wall-clock + read idle so multi-step work
        # (and long thinking streams) are not killed mid-flight.
        timeout = aiohttp.ClientTimeout(total=3_600, sock_read=900, connect=60)
        connector = aiohttp.TCPConnector(
            limit=24,
            ttl_dns_cache=300,
        )
        # Auto-continue after finish_reason=length / max_tokens until complete.
        # No artificial short-answer wall — keep going until the model finishes.
        max_length_continuations = 10_000
        length_continuations = 0
        # Retry once after repairing DeepSeek reasoning_content on tool turns.
        reasoning_repair_done = False
        # Soft API errors: keep going when we already have tool context.
        api_soft_failures = 0
        max_api_soft_failures = 16
        # Sticky force-answer after recoverable provider failures.
        force_answer_sticky = False
        # Empty-answer recovery (model thought but sent no content).
        empty_answer_retries = 0
        max_empty_answer_retries = 8
        # One OAuth/API re-auth attempt per turn (xAI 401 → refresh token).
        auth_refresh_done = False
        # Multi-epoch: soft walls compact; absolute total is safety only.
        max_total = max(1, int(getattr(runtime, "_max_react_steps", 10_000) or 10_000))
        epoch_size = max(
            16, int(getattr(runtime, "_epoch_react_steps", 256) or 256)
        )
        auto_continue = bool(getattr(runtime, "_react_auto_continue", True))
        max_stale_epochs = max(
            1, int(getattr(runtime, "_react_max_stale_epochs", 2) or 2)
        )
        epoch_index = 1
        productive_in_epoch = 0
        tool_batches_in_epoch = 0
        stale_epochs = 0
        tool_batches_this_turn = 0
        runtime._fingerprint_loop_hits = 0
        open_tasks_for_wall: list[str] = []
        with suppress(Exception):
            brief = getattr(runtime, "_session_brief", None)
            if brief is not None:
                open_tasks_for_wall = list(getattr(brief, "open_tasks", None) or [])
        # Coding / tool-enabled turns: Grok Build style — run until finished.
        run_until_done = bool(tools) or bool(all_tools)

        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector
        ) as http:
            for step in range(max_total):
                # Cooperative abort between ReAct steps (Stop generation).
                with suppress(Exception):
                    from remedy.core.turn_context import is_turn_aborted

                    if is_turn_aborted():
                        yield "@@aborted\n"
                        return

                # Soft epoch roll every epoch_size model rounds: compact + checkpoint.
                # Tools stay on — run until finished (not a tool-budget stop).
                if (
                    step > 0
                    and step % epoch_size == 0
                    and auto_continue
                    and not force_answer_sticky
                ):
                    unfinished = turn_has_unfinished_work(
                        runtime,
                        session_id=session_id,
                        tools_enabled=bool(tools or all_tools),
                        tool_steps_this_turn=tool_batches_this_turn,
                        open_tasks=open_tasks_for_wall or None,
                    )
                    coding_in_flight = run_until_done and (
                        unfinished
                        or tool_batches_this_turn > 0
                        or bool(tools or all_tools)
                    )
                    if coding_in_flight:
                        # Stale only when an epoch had *zero* tool calls (dead air).
                        # Failed tools still count as activity — keep recovering.
                        if tool_batches_in_epoch <= 0 and productive_in_epoch <= 0:
                            stale_epochs += 1
                        else:
                            stale_epochs = 0
                        if stale_epochs >= max_stale_epochs:
                            logger.warning(
                                "Stale epochs=%d at step=%d — safety pause "
                                "(no tool activity for many epochs)",
                                stale_epochs,
                                step,
                            )
                            yield (
                                "@@status:Paused after long idle (no tool activity) "
                                "— say continue to resume from the checkpoint\n"
                            )
                            with suppress(Exception):
                                md = runtime._maybe_auto_checkpoint(
                                    reason="step_wall",
                                    title="Idle safety pause",
                                    force=True,
                                )
                                if md:
                                    yield "@@checkpoint"
                            force_answer_sticky = True
                            tools = []
                        else:
                            epoch_index += 1
                            logger.info(
                                "ReAct soft epoch roll → epoch %d at step %d "
                                "(productive=%d tools=%d stale=%d)",
                                epoch_index,
                                step,
                                productive_in_epoch,
                                tool_batches_in_epoch,
                                stale_epochs,
                            )
                            yield (
                                f"@@status:Continuing until task finished "
                                f"(epoch {epoch_index}, step {step})…\n"
                            )
                            with suppress(Exception):
                                md = runtime._maybe_auto_checkpoint(
                                    reason="step_wall",
                                    title=f"Epoch {epoch_index - 1} complete",
                                    force=True,
                                )
                                if md:
                                    yield "@@checkpoint"
                            with suppress(Exception):
                                from remedy.memory.harness.send_policy import (
                                    slim_messages_mid_turn,
                                )

                                messages[:] = slim_messages_mid_turn(
                                    runtime,
                                    messages,
                                    session_id=str(
                                        getattr(runtime, "_session_id", "")
                                        or session_id
                                        or ""
                                    ),
                                    tool_result_char_cap=int(
                                        _TOOL_RESULT_CHAR_CAP or 0
                                    ),
                                )
                            messages.append(
                                epoch_continue_message(
                                    epoch=epoch_index - 1,
                                    total_step=step,
                                )
                            )
                            productive_in_epoch = 0
                            tool_batches_in_epoch = 0
                            # Keep tools; re-enable if a prior loop cleared them.
                            if all_tools:
                                tools = all_tools
                    elif step >= epoch_size and not run_until_done:
                        # Pure chat (tools never enabled) — wrap up.
                        force_answer_sticky = True
                    elif step >= epoch_size and run_until_done and all_tools:
                        # Tools enabled but model has not used them yet — nudge, keep going.
                        epoch_index += 1
                        tools = all_tools
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Keep going until the user request is finished. "
                                    "Use tools now (do not stop for a step count). "
                                    "Call list_dir / file_read / file_edit / bash_exec / "
                                    "mission_* / spread_run as needed and complete the work."
                                ),
                            }
                        )
                        productive_in_epoch = 0
                        tool_batches_in_epoch = 0

                is_final_step = step >= max_total - 1
                # Absolute safety wall only — soft epochs never force-answer alone.
                force_answer = (
                    is_final_step or not tools or force_answer_sticky
                )
                step_tools = None if force_answer else tools

                if force_answer and step > 0 and length_continuations == 0:
                    # Never ask for a "short" answer — complete full response.
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Stop calling tools. Using the information above, "
                                "give your complete final answer to the user now. "
                                "Be thorough and finish every section — "
                                "do not cut off mid-section or summarize-only."
                            ),
                        }
                    )

                # Mid-turn Memory Harness: re-slim after tool accumulation
                if step > 0 and getattr(runtime, "_harness_mode", "auto") != "off":
                    with suppress(Exception):
                        from remedy.memory.harness.send_policy import (
                            slim_messages_mid_turn,
                        )

                        messages[:] = slim_messages_mid_turn(
                            runtime,
                            messages,
                            session_id=str(
                                getattr(runtime, "_session_id", "") or session_id or ""
                            ),
                            tool_result_char_cap=int(_TOOL_RESULT_CHAR_CAP or 0),
                        )

                # Never send incomplete tool_calls/tool pairings (HTTP 400).
                messages[:] = ensure_tool_call_pairings(messages)
                # OpenAI-compatible providers (openai, deepseek, ollama, …) stream SSE.
                # Anthropic currently uses a single JSON response (stream=False).
                use_openai_sse = bool(
                    getattr(runtime._provider, "uses_openai_sse", True)
                )
                body = runtime._provider.build_body(
                    model=runtime._llm_model,
                    messages=messages,
                    tools=step_tools,
                    stream=use_openai_sse,
                    thinking_level=getattr(runtime, "_thinking_level", "high"),
                )

                collected: dict[str, Any] = {"content": None, "tool_calls": None}
                round_state = StreamRoundState()

                _llm_t0 = time.perf_counter()
                async with http.post(
                    endpoint, headers=headers, json=body
                ) as resp:
                    _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(
                            "LLM API error %d: %s", resp.status, text[:500]
                        )
                        with suppress(Exception):
                            from remedy.nanoswarm import get_swarm
                            from remedy.nanoswarm.events import SwarmEvent

                            get_swarm().dispatch(
                                SwarmEvent.provider_health(
                                    provider=getattr(runtime, "_llm_provider", None),
                                    model=getattr(runtime, "_llm_model", None),
                                    ok=False,
                                    latency_ms=_llm_ms,
                                    error=text[:200],
                                    status_code=int(resp.status),
                                )
                            )
                        # xAI (and similar): expired OAuth → refresh once, retry.
                        if (
                            resp.status in (401, 403)
                            and not auth_refresh_done
                            and str(runtime._llm_provider or "").lower() == "xai"
                        ):
                            auth_refresh_done = True
                            try:
                                from remedy.interfaces.xai_auth import (
                                    refresh_if_needed,
                                    resolve_bearer,
                                )

                                home = None
                                if getattr(runtime, "config", None) is not None:
                                    hd = getattr(runtime.config, "home_dir", None)
                                    if hd:
                                        from pathlib import Path

                                        home = Path(hd).expanduser()
                                refresh_if_needed(home)
                                new_token = resolve_bearer(home)
                                if new_token and new_token != runtime._llm_api_key:
                                    runtime._llm_api_key = new_token
                                    headers = runtime._provider.auth_headers(
                                        runtime._llm_api_key
                                    )
                                    logger.warning(
                                        "xAI credentials refreshed after HTTP %s; retrying",
                                        resp.status,
                                    )
                                    yield (
                                        "\n[auth] Refreshed xAI session; "
                                        "retrying request…\n"
                                    )
                                    continue
                            except Exception as auth_exc:
                                logger.debug("xAI re-auth failed: %s", auth_exc)
                            # Refresh failed → clear soft-continue noise with guidance.
                            yield (
                                "\n[auth required] xAI session expired or rejected. "
                                "Sign in again in Settings (Sign in with xAI) or "
                                "update your API key.\n"
                            )
                            return
                        # DeepSeek thinking mode: tool turns require reasoning_content.
                        if (
                            resp.status == 400
                            and "reasoning_content" in text.lower()
                            and not reasoning_repair_done
                        ):
                            reasoning_repair_done = True
                            if repair_reasoning_content_in_messages(messages):
                                logger.warning(
                                    "Repaired missing reasoning_content on tool "
                                    "turns; retrying request"
                                )
                                yield (
                                    "\n[provider fix] Restored thinking-mode "
                                    "reasoning for tool turns; continuing…\n"
                                )
                                continue
                        api_soft_failures += 1
                        # Do not hard-stop the whole turn if we can still answer.
                        if api_soft_failures <= max_api_soft_failures:
                            yield (
                                f"\n[LLM notice — HTTP {resp.status}; "
                                f"continuing]\n{text[:240]}\n"
                            )
                            tools = []
                            force_answer_sticky = True
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "The model API returned an error. "
                                        "Using any tool results already gathered, "
                                        "give your best complete answer now. "
                                        "Do not call tools."
                                    ),
                                }
                            )
                            continue
                        yield (
                            f"\n[LLM ERROR — HTTP {resp.status}]\n"
                            f"{text[:500]}\n[END LLM ERROR]\n"
                            "I hit repeated API errors but will try one last "
                            "answer from context.\n"
                        )
                        tools = []
                        force_answer_sticky = True
                        continue

                    with suppress(Exception):
                        from remedy.nanoswarm import get_swarm
                        from remedy.nanoswarm.events import SwarmEvent

                        get_swarm().dispatch(
                            SwarmEvent.provider_health(
                                provider=getattr(runtime, "_llm_provider", None),
                                model=getattr(runtime, "_llm_model", None),
                                ok=True,
                                latency_ms=_llm_ms,
                                status_code=200,
                            )
                        )

                    # Live-stream final-answer rounds (no tools this step).
                    # Buffer when tools are enabled — DeepSeek-class models
                    # often dump DSML tool markup as content if we stream live.
                    stream_live = step_tools is None

                    headers_map = getattr(resp, "headers", None) or {}
                    content_type = str(
                        headers_map.get("Content-Type")
                        or headers_map.get("content-type")
                        or ""
                    ).lower()
                    # Prefer real response type; DeepSeek/OpenRouter return event-stream.
                    is_event_stream = "event-stream" in content_type
                    if use_openai_sse or is_event_stream:
                        content_iter = resp.content.__aiter__()
                        # DeepSeek thinking can pause for minutes between tokens.
                        # 120s was killing long reasoner streams mid-thought.
                        sse_idle_timeout = 900.0
                        while True:
                            try:
                                line = await asyncio.wait_for(
                                    content_iter.__anext__(),
                                    timeout=sse_idle_timeout,
                                )
                            except StopAsyncIteration:
                                break
                            except TimeoutError:
                                logger.warning(
                                    "SSE stream idle >%.0fs; ending this model round "
                                    "(will continue/promote reasoning if any)",
                                    sse_idle_timeout,
                                )
                                break
                            line_text = line.decode("utf-8").strip()
                            if line_text == "data: [DONE]":
                                break
                            chunk = parse_sse_data_line(line_text)
                            if chunk is None:
                                continue
                            # Provider usage (often only on final SSE chunk)
                            try:
                                from remedy.core.usage import usage_from_provider_payload

                                u = usage_from_provider_payload(
                                    chunk,
                                    model=getattr(runtime, "_llm_model", None),
                                    provider=getattr(runtime, "_llm_provider", None),
                                )
                                if u:
                                    try:
                                        from remedy.core.usage import observe_provider_usage
                                        from remedy.core.usage_ledger import (
                                            record_usage_event,
                                        )
                                        from remedy.nanoswarm.token_nanobot import (
                                            get_token_nanobot,
                                        )

                                        pt = int(u.get("prompt_tokens") or 0)
                                        ct = int(u.get("completion_tokens") or 0)
                                        est = int(get_token_nanobot().last_estimate or 0)
                                        prov = getattr(runtime, "_llm_provider", None)
                                        mod = getattr(runtime, "_llm_model", None)
                                        if pt > 0 and est > 0:
                                            observe_provider_usage(
                                                est,
                                                pt,
                                                provider=prov,
                                                model=mod,
                                            )
                                        if pt or ct:
                                            with suppress(Exception):
                                                from remedy.core.session_quality import (
                                                    get_session_quality,
                                                )

                                                get_session_quality(
                                                    str(
                                                        getattr(runtime, "_session_id", "")
                                                        or ""
                                                    )
                                                ).record_turn(
                                                    prompt_tokens=pt,
                                                    completion_tokens=ct,
                                                )
                                            with suppress(Exception):
                                                record_usage_event(
                                                    session_id=str(
                                                        getattr(runtime, "_session_id", "")
                                                        or ""
                                                    )
                                                    or None,
                                                    provider=prov,
                                                    model=mod,
                                                    prompt_tokens=pt,
                                                    completion_tokens=ct,
                                                    total_tokens=int(
                                                        u.get("total_tokens") or (pt + ct)
                                                    ),
                                                    estimated_cost_usd=float(
                                                        u.get("estimated_cost_usd") or 0
                                                    ),
                                                    source=str(u.get("source") or "provider"),
                                                )
                                    except Exception:
                                        pass
                                    yield (
                                        "@@usage:"
                                        + json.dumps(u, separators=(",", ":"))
                                    )
                            except Exception:
                                pass
                            r_before = len(''.join(round_state.reasoning_parts))
                            live = apply_openai_sse_chunk(
                                round_state, chunk, stream_live=stream_live
                            )
                            r_after = ''.join(round_state.reasoning_parts)
                            if len(r_after) > r_before:
                                yield f'@@thinking:{r_after[r_before:]}'
                            if live:
                                produced_user_text = True
                                yield live
                    else:
                        data = await resp.json()
                        try:
                            from remedy.core.usage import usage_from_provider_payload

                            u = usage_from_provider_payload(
                                data,
                                model=getattr(runtime, "_llm_model", None),
                                provider=getattr(runtime, "_llm_provider", None),
                            )
                            if u:
                                yield (
                                    "@@usage:"
                                    + json.dumps(u, separators=(",", ":"))
                                )
                        except Exception:
                            pass
                        parsed = runtime._provider.extract_response(data)
                        content = parsed.get("content")
                        if content:
                            round_state.content_parts.append(content)
                        # Capture provider reasoning for tool-turn replay.
                        reason = (
                            parsed.get("reasoning_content")
                            or parsed.get("reasoning")
                            or ""
                        )
                        if isinstance(reason, str) and reason.strip():
                            round_state.reasoning_parts.append(reason.strip())
                        raw_tcs = parsed.get("tool_calls")
                        if raw_tcs:
                            round_state.tool_call_acc = dict(enumerate(raw_tcs))
                        collected = {**collected, **parsed}

                    content_parts = round_state.content_parts
                    reasoning_parts = round_state.reasoning_parts

                tool_calls_list = round_state.tool_calls_list(collected)
                reasoning_out = round_state.reasoning_out

                # Finalize text. Live-stream already yielded tokens when tools off.
                text_out = finalize_round_text(round_state, tool_calls_list)
                # Never treat DSML / text-tool dumps as user-visible answer text.
                if text_out and _looks_like_pseudo_tools(text_out):
                    recovered_preview = _parse_pseudo_tool_calls(text_out)
                    clean = strip_tool_markup(text_out)
                    # Keep only non-markup prose (if any) for the bubble.
                    text_out = clean if clean and not _looks_like_pseudo_tools(clean) else ""
                    if not tool_calls_list and recovered_preview:
                        # Force recovery path below even if tools were off this round.
                        pass
                if (
                    text_out
                    and stream_live
                    and not content_parts
                    and reasoning_parts
                    and not tool_calls_list
                    and not _looks_like_pseudo_tools(text_out)
                ):
                    yield text_out
                    produced_user_text = True
                if text_out:
                    collected["content"] = text_out

                # Recovery: model wrote tool calls as plain text / DSML → run them for real.
                raw_round = finalize_round_text(round_state, tool_calls_list)
                if (
                    not tool_calls_list
                    and raw_round
                    and _looks_like_pseudo_tools(raw_round)
                    and all_tools
                    and not pseudo_recovery_done
                    and not force_answer
                ):
                    recovered = _parse_pseudo_tool_calls(raw_round)
                    if recovered:
                        pseudo_recovery_done = True
                        tools = all_tools  # ensure schemas stay available
                        recovered = normalize_tool_calls(recovered)
                        yield "@@tool_calls"
                        messages.append(
                            build_assistant_api_message(
                                content=(
                                    "Using tools now (recovered from a non-native tool dump)."
                                ),
                                tool_calls=recovered,
                                reasoning_content=reasoning_out or None,
                            )
                        )
                        batch_tool_msgs: list[dict[str, Any]] = []
                        async for event, tool_msg in execute_tool_calls(runtime,
                            recovered,
                            seen_fps=seen_fps,
                            result_cache=result_cache,
                        ):
                            if event.startswith("@@"):
                                yield event
                            if tool_msg:
                                messages.append(tool_msg)
                                batch_tool_msgs.append(tool_msg)
                        if (
                            not recovery_nudge_done
                            and batch_has_tool_errors(batch_tool_msgs)
                        ):
                            recovery_nudge_done = True
                            empty = batch_has_empty_search(batch_tool_msgs)
                            need_appr = batch_has_approval_required(batch_tool_msgs)
                            messages.append(
                                recovery_nudge_message(
                                    empty_search=empty, approval=need_appr
                                )
                            )
                            with suppress(Exception):
                                md = runtime._maybe_auto_checkpoint(
                                    reason="recovery",
                                    title="After tool failure",
                                    force=True,
                                )
                                if md:
                                    yield "@@checkpoint"
                        with suppress(Exception):
                            runtime._maybe_auto_checkpoint(reason="auto")
                        continue

                if text_out and (not tool_calls_list or force_answer):
                    # Don't ship faux tool syntax as the final answer.
                    if (
                        raw_round
                        and _looks_like_pseudo_tools(raw_round)
                        and all_tools
                        and not force_answer
                        and pseudo_nudge_count < 1
                        and not pseudo_recovery_done
                    ):
                        pseudo_nudge_count += 1
                        tools = all_tools
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Do not write tool calls as text or DSML/XML. "
                                    "Use the function-calling API now "
                                    "(comfyui / local_discover / file_read / "
                                    "list_dir / bash_exec), or answer from context."
                                ),
                            }
                        )
                        continue
                    # Narrating "I'm processing…" without tools looks stuck in the UI.
                    # One hard nudge: call tools now, don't restate intent.
                    if (
                        not tool_calls_list
                        and all_tools
                        and not force_answer
                        and false_progress_nudge_count < 1
                        and looks_like_false_progress(text_out)
                    ):
                        false_progress_nudge_count += 1
                        tools = all_tools
                        force_answer_sticky = False
                        messages.append(
                            {
                                "role": "assistant",
                                "content": text_out,
                            }
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Stop narrating intent. Use native function-calling "
                                    "tools now (list_dir / file_read / file_write / "
                                    "file_edit / bash_exec / comfyui / mission_start) "
                                    "and keep going until the user request is finished. "
                                    "Do not reply with only a status line."
                                ),
                            }
                        )
                        logger.info(
                            "False-progress nudge after step %d (no tool_calls)",
                            step + 1,
                        )
                        continue
                    if stream_live and produced_user_text:
                        # Hit max_tokens mid-answer → seamless continuation.
                        if (
                            round_state.hit_length_limit
                            and length_continuations < max_length_continuations
                            and not tool_calls_list
                        ):
                            length_continuations += 1
                            logger.info(
                                "Stream hit length limit (finish_reason=%s); "
                                "auto-continuing (%d/%d)",
                                round_state.finish_reason,
                                length_continuations,
                                max_length_continuations,
                            )
                            messages.append(
                                {"role": "assistant", "content": text_out}
                            )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Your previous message was cut off by the output "
                                        "token limit. Continue exactly where you stopped — "
                                        "do not restart, renumber from scratch, or summarize "
                                        "what you already wrote. Pick up mid-sentence if needed."
                                    ),
                                }
                            )
                            tools = []  # keep producing prose
                            continue
                        return
                    if not stream_live:
                        # Final safety: never yield markup-only blobs.
                        if text_out and not _looks_like_pseudo_tools(text_out):
                            yield text_out
                            produced_user_text = True
                        if (
                            round_state.hit_length_limit
                            and length_continuations < max_length_continuations
                            and not tool_calls_list
                        ):
                            length_continuations += 1
                            messages.append(
                                {"role": "assistant", "content": text_out}
                            )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Continue exactly where you stopped — do not restart."
                                    ),
                                }
                            )
                            tools = []
                            continue
                    return

                if not tool_calls_list or force_answer:
                    # Empty content after tools/thinking: never soft-give-up while
                    # we still have budget. DeepSeek often leaves content blank
                    # after a long reasoning stream — promote reasoning first.
                    if not produced_user_text:
                        # 1) Reasoning-only answer (common for reasoner models).
                        if reasoning_out and not _looks_like_pseudo_tools(
                            reasoning_out
                        ):
                            yield reasoning_out
                            produced_user_text = True
                            if (
                                round_state.hit_length_limit
                                and length_continuations
                                < max_length_continuations
                            ):
                                length_continuations += 1
                                messages.append(
                                    {
                                        "role": "assistant",
                                        "content": reasoning_out,
                                        "reasoning_content": reasoning_out,
                                    }
                                )
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "Continue your final answer exactly where "
                                            "you stopped. Do not restart or summarize."
                                        ),
                                    }
                                )
                                tools = []
                                force_answer_sticky = True
                                continue
                            return
                        # 2) Retry synthesis — do not abandon the user mid-task.
                        if (
                            empty_answer_retries < max_empty_answer_retries
                            and not is_final_step
                        ):
                            empty_answer_retries += 1
                            force_answer_sticky = True
                            tools = []
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "You gathered context (tools and/or thinking) "
                                        "but returned no final answer text. "
                                        "Write the complete final answer now as plain "
                                        "chat content — full review, not a stub. "
                                        "Do not call tools."
                                    ),
                                }
                            )
                            logger.info(
                                "Empty answer retry %d/%d after step %d",
                                empty_answer_retries,
                                max_empty_answer_retries,
                                step + 1,
                            )
                            continue
                        # 3) Last resort only after retries exhausted.
                        yield (
                            "I gathered context but the model returned an empty "
                            "final message after several retries. Please resend "
                            "or ask me to continue from where I left off."
                        )
                    return

                # Filter out exact repeats of prior tool calls this turn.
                fresh_calls = normalize_tool_calls(
                    filter_fresh_tool_calls(tool_calls_list, seen_fps)
                )
                if not fresh_calls:
                    # Model is looping the same tools — feed cached results, nudge
                    # different actions. Only force-answer after repeated loops with
                    # no unfinished mission/open work.
                    looped = normalize_tool_calls(tool_calls_list)
                    messages.append(
                        build_assistant_api_message(
                            content=collected.get("content"),
                            tool_calls=looped,
                            reasoning_content=reasoning_out or "",
                        )
                    )
                    for tc in looped:
                        fp = _tool_call_fingerprint(tc)
                        cached = result_cache.get(fp, "(already retrieved)")
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": cached,
                            }
                        )
                    loop_hits = int(getattr(runtime, "_fingerprint_loop_hits", 0) or 0) + 1
                    runtime._fingerprint_loop_hits = loop_hits
                    unfinished_loop = turn_has_unfinished_work(
                        runtime,
                        session_id=session_id,
                        tools_enabled=bool(all_tools),
                        tool_steps_this_turn=tool_batches_this_turn,
                        open_tasks=open_tasks_for_wall or None,
                    )
                    if unfinished_loop and loop_hits < 3 and all_tools:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Those exact tool calls already ran (results above). "
                                    "Do not repeat them. Use different paths/args, "
                                    "mission_update / mission_verify, file_edit, or "
                                    "spread_run/job_run — and finish the remaining work."
                                ),
                            }
                        )
                        tools = all_tools
                        continue
                    # Jump toward final answer on next iteration.
                    tools = []
                    continue

                messages.append(
                    build_assistant_api_message(
                        content=collected.get("content"),
                        tool_calls=fresh_calls,
                        # DeepSeek thinking mode: MUST pass reasoning back on tool turns.
                        reasoning_content=reasoning_out or "",
                    )
                )

                batch_tool_msgs: list[dict[str, Any]] = []
                async for event, tool_msg in execute_tool_calls(runtime,
                    fresh_calls,
                    seen_fps=seen_fps,
                    result_cache=result_cache,
                ):
                    if event.startswith("@@"):
                        yield event
                    if tool_msg:
                        messages.append(tool_msg)
                        batch_tool_msgs.append(tool_msg)

                logger.debug(
                    "ReAct step %d executed %d tool call(s)",
                    step + 1,
                    len(fresh_calls),
                )
                tool_batches_this_turn += 1
                tool_batches_in_epoch += 1
                if is_productive_tool_batch(batch_tool_msgs):
                    productive_in_epoch += 1

                # Soft recovery: if tools failed or search empty, nudge once.
                if (
                    not recovery_nudge_done
                    and not force_answer
                    and batch_has_tool_errors(batch_tool_msgs)
                ):
                    recovery_nudge_done = True
                    empty = batch_has_empty_search(batch_tool_msgs)
                    need_appr = batch_has_approval_required(batch_tool_msgs)
                    messages.append(
                        recovery_nudge_message(empty_search=empty, approval=need_appr)
                    )
                    logger.info(
                        "Injected tool recovery nudge after step %d "
                        "(empty_search=%s approval=%s)",
                        step + 1,
                        empty,
                        need_appr,
                    )
                    with suppress(Exception):
                        runtime._maybe_auto_checkpoint(
                            reason="recovery",
                            title="After tool failure",
                            force=True,
                        )
                # Mission done-gate: steps done but verify not passed → force verify
                with suppress(Exception):
                    from remedy.core.mission import MissionStore

                    home = getattr(getattr(runtime, "config", None), "home_dir", None)
                    mid = str(getattr(runtime, "_session_id", "") or "") or None
                    m = MissionStore(home).latest(mid)
                    if (
                        m is not None
                        and m.status == "active"
                        and m.verify_command
                        and m.verify_status != "passed"
                        and m.steps
                        and all(s.status in ("done", "skipped") for s in m.steps)
                        and not force_answer
                        and not getattr(runtime, "_mission_gate_nudge_done", False)
                    ):
                        runtime._mission_gate_nudge_done = True
                        messages.append(
                            mission_verify_gate_message(m.verify_command)
                        )
                        logger.info("Injected mission verify gate nudge")
                with suppress(Exception):
                    runtime._maybe_auto_checkpoint(reason="auto")
                if is_final_step:
                    with suppress(Exception):
                        md = runtime._maybe_auto_checkpoint(
                            reason="step_wall",
                            title="Absolute safety step ceiling",
                            force=True,
                        )
                        if md:
                            yield "@@checkpoint"

        # Exhausted absolute safety steps without a streamed answer.
        if not produced_user_text:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Using all tool results and context above, write the "
                        "complete final answer now. Be thorough — do not give a "
                        "one-line stub or say you cannot answer if context exists."
                    ),
                }
            )
            messages[:] = ensure_tool_call_pairings(messages)
            use_openai_sse = bool(
                getattr(runtime._provider, "uses_openai_sse", True)
            )
            body = runtime._provider.build_body(
                model=runtime._llm_model,
                messages=messages,
                tools=None,
                stream=use_openai_sse,
                thinking_level=getattr(runtime, "_thinking_level", "high"),
            )
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=900, sock_read=900)
                ) as http2, http2.post(
                    endpoint, headers=headers, json=body
                ) as resp:
                    if resp.status == 200:
                        headers_map = getattr(resp, "headers", None) or {}
                        content_type = str(
                            headers_map.get("Content-Type")
                            or headers_map.get("content-type")
                            or ""
                        ).lower()
                        if use_openai_sse or "event-stream" in content_type:
                            async for line in resp.content:
                                line_text = line.decode("utf-8").strip()
                                if not line_text or line_text.startswith(":"):
                                    continue
                                if line_text == "data: [DONE]":
                                    break
                                if line_text.startswith("data: "):
                                    line_text = line_text[6:]
                                try:
                                    chunk = json.loads(line_text)
                                except json.JSONDecodeError:
                                    continue
                                delta = (chunk.get("choices") or [{}])[0].get(
                                    "delta"
                                ) or {}
                                piece = delta.get("content")
                                if piece:
                                    produced_user_text = True
                                    yield piece
                                # Also surface trailing reasoning as answer if
                                # content stayed empty (DeepSeek reasoner).
                                reason = (
                                    delta.get("reasoning_content")
                                    or delta.get("reasoning")
                                )
                                if (
                                    not piece
                                    and isinstance(reason, str)
                                    and reason
                                ):
                                    produced_user_text = True
                                    yield reason
                        else:
                            data = await resp.json()
                            parsed = runtime._provider.extract_response(data)
                            piece = parsed.get("content") or parsed.get(
                                "reasoning_content"
                            )
                            if piece:
                                produced_user_text = True
                                yield str(piece)
            except Exception:
                logger.debug("final synthesis failed", exc_info=True)
        if not produced_user_text:
            yield (
                "I finished the tool loop but still have no final model text. "
                "Ask me to **continue** or restate the request and I will resume "
                "from the context already gathered."
            )
        # Compound learning + speculative warm for next turn
        from remedy.core.agent_post_turn import schedule_post_turn_prep

        schedule_post_turn_prep(runtime, message=message or "")
    except Exception as e:
        logger.exception("LLM stream failed")
        # Never leave the user with only a stack-looking error — give a path forward.
        yield (
            f"\n[LLM STREAM EXCEPTION]\n{e}\n[END LLM STREAM EXCEPTION]\n\n"
            "Something went wrong talking to the model mid-turn. "
            "Try again, switch model, or ask a narrower question. "
            "Your session history is intact."
        )


