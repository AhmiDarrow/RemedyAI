"""ReAct LLM stream loop (OpenCode-grade).

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
    batch_has_tool_errors,
    recovery_nudge_message,
    strip_tool_markup,
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
    """Call the LLM with a smooth ReAct loop (OpenCode-grade).

    Yields status tokens prefixed with '@@' for tool-call lifecycle events.
    Never leaves the user with a bare "tool limit" dead-end — final step
    always forces a plain-text answer (or a short synthesis).

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
                context = (
                    (context or "")
                    + "\n\n## Plan mode (active)\n"
                    "You are exploring and planning — do **not** edit files, run shell, "
                    "or mutate the system. Use plan_save to store a structured plan with "
                    "clear steps and risks, then summarize for the user. "
                    "They will switch to Build mode to execute."
                )
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
        # Continuity layer: single ContextSnapshot (tokens, policy, remedies, brief)
        with suppress(Exception):
            if runtime._harness_mode == "auto":
                from remedy.core.context_snapshot import build_context_snapshot
                from remedy.memory.harness.compressor import (
                    compression_nudge_message,
                    estimate_tokens,
                    heuristic_merge_from_history,
                )
                from remedy.memory.harness.pruner import prune_messages_for_send

                provider = str(
                    getattr(runtime.config, "provider", None)
                    or getattr(runtime.config, "llm_provider", "")
                    or ""
                )
                model = str(
                    getattr(runtime.config, "model", None)
                    or getattr(runtime.config, "llm_model", "")
                    or ""
                )
                project_path = str(
                    getattr(runtime.config, "project_path", None)
                    or getattr(runtime, "_project_path", None)
                    or ""
                ) or None
                sid = str(getattr(runtime, "_session_id", "") or "")
                snap = build_context_snapshot(
                    messages=messages,
                    user_text=message or "",
                    brief=getattr(runtime, "_session_brief", None),
                    session_id=sid,
                    provider=provider or None,
                    model=model or None,
                    min_pct=runtime._harness_min_pct,
                    max_pct=runtime._harness_max_pct,
                    project_path=project_path,
                )
                runtime._last_context_snapshot = snap
                runtime._last_send_messages = list(messages)
                est = snap.token_estimate
                level = snap.nudge

                # Inject policy + quality remedies + project pins as system notes
                injects: list[str] = []
                if snap.policy_system:
                    injects.append(snap.policy_system)
                if snap.remedy_system:
                    injects.append(snap.remedy_system)
                with suppress(Exception):
                    from remedy.core.project_learning import pinned_constraints_block

                    pin = pinned_constraints_block(project_path)
                    if pin:
                        injects.append(pin)
                if injects:
                    messages.insert(
                        -1,
                        {
                            "role": "system",
                            "content": "\n\n".join(injects),
                        },
                    )

                if level == "strong":
                    tokens_before = est
                    messages[:] = prune_messages_for_send(
                        messages,
                        max_tool_chars=max(
                            4_000, (_TOOL_RESULT_CHAR_CAP or 64_000) // 4
                        ),
                        dedupe_tools=True,
                        collapse_completed_tools=True,
                        keep_recent_tool_pairs=4,
                    )
                    with suppress(Exception):
                        brief = getattr(runtime, "_session_brief", None)
                        if brief is not None:
                            from remedy.core.session_quality import get_session_quality
                            from remedy.memory.harness.quality import (
                                review_compress_quality,
                            )

                            pre_hist = list(messages)
                            runtime._session_brief = heuristic_merge_from_history(
                                brief, messages, intent_hint=message
                            )
                            tokens_after = estimate_tokens(
                                messages,
                                provider=provider or None,
                                model=model or None,
                            )
                            q = review_compress_quality(
                                messages_before=pre_hist,
                                brief=runtime._session_brief,
                                tokens_before=tokens_before,
                                tokens_after=tokens_after,
                            )
                            get_session_quality(sid).record_compress(
                                tokens_before=tokens_before,
                                tokens_after=tokens_after,
                                quality=q,
                                source="auto_strong",
                            )
                    messages.insert(-1, compression_nudge_message("strong"))
                    with suppress(Exception):
                        from remedy.core.metrics import default_registry

                        default_registry.counter(
                            "remedy_context_auto_compress_total", level="strong"
                        ).inc()
                elif level == "soft":
                    # Soft: structural collapse of old tools without hard cap first
                    with suppress(Exception):
                        messages[:] = prune_messages_for_send(
                            messages,
                            dedupe_tools=True,
                            collapse_completed_tools=True,
                            keep_recent_tool_pairs=6,
                        )
                    messages.insert(-1, compression_nudge_message(level))
                with suppress(Exception):
                    from remedy.core.metrics import default_registry

                    default_registry.gauge("remedy_context_tokens_estimate").set(
                        float(est)
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
            tools = (
                all_tools
                if should_enable_tools(
                    message, all_tools, has_attachments=bool(attachments)
                )
                or bool(
                    re.search(
                        r"\b(comfy|image|picture|nebula|spacey|generate|draw|illustrat)\b",
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
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector
        ) as http:
            for step in range(runtime._max_react_steps):
                is_final_step = step >= runtime._max_react_steps - 1
                # Only force a final answer at the true step wall (or sticky).
                # Early force-answer (old: step>=8) made long tool chains "stuck".
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
                            messages.append(recovery_nudge_message())
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
                    # Model is looping the same tools — force a final answer next.
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
                    # Jump toward final answer on next iteration.
                    tools = []  # disable further tool schemas
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

                # Soft recovery: if tools failed, nudge the model once to
                # try alternate paths/commands before answering.
                if (
                    not recovery_nudge_done
                    and not force_answer
                    and batch_has_tool_errors(batch_tool_msgs)
                ):
                    recovery_nudge_done = True
                    messages.append(recovery_nudge_message())
                    logger.info(
                        "Injected tool recovery nudge after step %d (RECOVERY_NUDGE)",
                        step + 1,
                    )
                    with suppress(Exception):
                        runtime._maybe_auto_checkpoint(
                            reason="recovery",
                            title="After tool failure",
                            force=True,
                        )
                with suppress(Exception):
                    runtime._maybe_auto_checkpoint(reason="auto")
                if is_final_step:
                    with suppress(Exception):
                        md = runtime._maybe_auto_checkpoint(
                            reason="step_wall",
                            title="Approaching step limit",
                            force=True,
                        )
                        if md:
                            yield "@@checkpoint"

        # Exhausted steps without a streamed answer — full synthesis, not a stub.
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


