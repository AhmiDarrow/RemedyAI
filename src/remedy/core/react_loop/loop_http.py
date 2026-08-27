"""ReAct HTTP round — POST, error recovery, consume stream into round_state.

Looks up patched names on ``remedy.core.react_loop.loop`` at call time.
Mutates bag ``s``; sets ``s.turn_complete`` on fatal / abort paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


async def run_react_http(
    s: Any,
    http: Any,
    *,
    step: int,
    helpers: Any,
) -> AsyncIterator[str]:
    """Run one provider HTTP round for ``step``. Updates collected / round_state on ``s``."""
    from remedy.core.react_loop.loop_bindings import bind_loop_module
    _lb = bind_loop_module()
    aiohttp = _lb["aiohttp"]
    LlmBinding = _lb["LlmBinding"]  # noqa: N806
    get_llm_binding = _lb["get_llm_binding"]
    set_llm_binding = _lb["set_llm_binding"]
    build_step_request_body = _lb["build_step_request_body"]
    apply_build_engine_after_batch = _lb["apply_build_engine_after_batch"]
    _turn_tier_of = _lb["_turn_tier_of"]
    _resolve_and_apply_tools_fn = _lb["_resolve_and_apply_tools_fn"]
    _wait_rmb_ready_abortable = _lb["_wait_rmb_ready_abortable"]
    consume_llm_http_response = _lb["consume_llm_http_response"]
    sanitize_chat_body = _lb["sanitize_chat_body"]
    repair_reasoning_content_in_messages = _lb["repair_reasoning_content_in_messages"]
    repair_tool_arguments_in_messages = _lb["repair_tool_arguments_in_messages"]
    strip_broken_tool_call_turns = _lb["strip_broken_tool_call_turns"]
    _sleep_abortable = _lb["_sleep_abortable"]
    _http_session = _lb["_http_session"]
    _log_llm = _lb["_log_llm"]
    _stopped_note = _lb["_stopped_note"]
    _take_nudges = _lb["_take_nudges"]
    _steer_message = _lb["_steer_message"]
    _browse_tool_ok = _lb["_browse_tool_ok"]
    execute_tool_calls = _lb["execute_tool_calls"]
    _provider_bits_fn = _lb["_provider_bits_fn"]
    _rearm_agency_tools_fn = _lb["_rearm_agency_tools_fn"]
    _RATE_LIMIT_MAX_RETRIES = _lb["_RATE_LIMIT_MAX_RETRIES"]  # noqa: N806
    _is_rate_limited = _lb["_is_rate_limited"]
    _pace_before_request = _lb["_pace_before_request"]
    _rate_limit_wait = _lb["_rate_limit_wait"]
    _await_or_abort = _lb["_await_or_abort"]
    inject_phase_nudge = _lb["inject_phase_nudge"]
    record_tool_batch_stats = _lb["record_tool_batch_stats"]
    _is_billing_llm_api_error = _lb["_is_billing_llm_api_error"]
    _is_fatal_llm_api_error = _lb["_is_fatal_llm_api_error"]
    _is_thinking_tool_choice_error = _lb["_is_thinking_tool_choice_error"]
    fatal_billing_error_message = _lb["fatal_billing_error_message"]
    fatal_model_error_message = _lb["fatal_model_error_message"]
    repeated_provider_error_message = _lb["repeated_provider_error_message"]
    _TOOL_RESULT_CHAR_CAP = _lb["_TOOL_RESULT_CHAR_CAP"]  # noqa: N806
    _looks_like_pseudo_tools = _lb["_looks_like_pseudo_tools"]
    _parse_pseudo_tool_calls = _lb["_parse_pseudo_tool_calls"]
    _tool_call_fingerprint = _lb["_tool_call_fingerprint"]
    agency_rearm_nudge_message = _lb["agency_rearm_nudge_message"]
    agency_tool_promise_claim = _lb["agency_tool_promise_claim"]
    batch_has_approval_required = _lb["batch_has_approval_required"]
    batch_has_empty_or_spam_write = _lb["batch_has_empty_or_spam_write"]
    batch_has_empty_search = _lb["batch_has_empty_search"]
    batch_has_tool_errors = _lb["batch_has_tool_errors"]
    clip_appended_source_dump = _lb["clip_appended_source_dump"]
    collapse_repeated_sentences = _lb["collapse_repeated_sentences"]
    epoch_continue_message = _lb["epoch_continue_message"]
    is_serial_explore_batch = _lb["is_serial_explore_batch"]
    looks_like_false_progress = _lb["looks_like_false_progress"]
    looks_like_leaked_scratchpad = _lb["looks_like_leaked_scratchpad"]
    looks_like_safety_refusal = _lb["looks_like_safety_refusal"]
    message_asks_to_stop = _lb["message_asks_to_stop"]
    mission_verify_gate_message = _lb["mission_verify_gate_message"]
    post_tools_user_summary_nudge = _lb["post_tools_user_summary_nudge"]
    recovery_nudge_message = _lb["recovery_nudge_message"]
    speed_batch_nudge_message = _lb["speed_batch_nudge_message"]
    strip_stream_status_noise = _lb["strip_stream_status_noise"]
    strip_tool_markup = _lb["strip_tool_markup"]
    turn_has_unfinished_work = _lb["turn_has_unfinished_work"]
    unfinished_work_blocks_final = _lb["unfinished_work_blocks_final"]
    unfinished_work_hard_stop_message = _lb["unfinished_work_hard_stop_message"]
    unfinished_work_nudge_message = _lb["unfinished_work_nudge_message"]
    StreamRoundState = _lb["StreamRoundState"]  # noqa: N806
    build_assistant_api_message = _lb["build_assistant_api_message"]
    ensure_tool_call_pairings = _lb["ensure_tool_call_pairings"]
    filter_fresh_tool_calls = _lb["filter_fresh_tool_calls"]
    finalize_round_text = _lb["finalize_round_text"]
    normalize_tool_calls = _lb["normalize_tool_calls"]
    _current_abort_event = _lb["_current_abort_event"]
    set_turn_force_tool_choice = _lb["set_turn_force_tool_choice"]
    set_turn_thinking_level = _lb["set_turn_thinking_level"]
    set_turn_tool_choice_required_blocked = _lb["set_turn_tool_choice_required_blocked"]
    turn_max_react_steps = _lb["turn_max_react_steps"]
    turn_sleev_force_direct = _lb["turn_sleev_force_direct"]
    turn_thinking_level = _lb["turn_thinking_level"]
    logger = _lb["logger"]
    json = _lb["json"]
    time = _lb["time"]
    asyncio = _lb["asyncio"]
    suppress = _lb["suppress"]
    from remedy.core.react_loop.loop_bindings import unpack_state
    _st = unpack_state(s)
    run_until_done = _st["run_until_done"]
    build_state = _st["build_state"]
    turn = _st["turn"]
    plan_mode = _st["plan_mode"]
    attachments = _st["attachments"]
    session_id = _st["session_id"]
    message = _st["message"]
    runtime = _st["runtime"]
    prep = _st["prep"]
    boot = _st["boot"]
    messages = _st["messages"]
    history = _st["history"]
    all_tools = _st["all_tools"]
    tools = _st["tools"]
    browse_pre_url = _st["browse_pre_url"]
    clear_goals_only = _st["clear_goals_only"]
    pure_action_kick = _st["pure_action_kick"]
    open_only_browse = _st["open_only_browse"]
    page_interaction = _st["page_interaction"]
    seen_fps = _st["seen_fps"]
    result_cache = _st["result_cache"]
    produced_user_text = _st["produced_user_text"]
    pseudo_recovery_done = _st["pseudo_recovery_done"]
    pseudo_nudge_count = _st["pseudo_nudge_count"]
    false_progress_nudge_count = _st["false_progress_nudge_count"]
    zero_tools_hard_block_count = _st["zero_tools_hard_block_count"]
    mono_fp_last = _st["mono_fp_last"]
    mono_fp_hits = _st["mono_fp_hits"]
    mono_explore_injected = _st["mono_explore_injected"]
    scratchpad_nudge_count = _st["scratchpad_nudge_count"]
    tools_executed_this_turn = _st["tools_executed_this_turn"]
    recovery_nudge_done = _st["recovery_nudge_done"]
    speed_batch_nudge_done = _st["speed_batch_nudge_done"]
    serial_explore_streak = _st["serial_explore_streak"]
    _bind = _st["_bind"]
    _adapter = _st["_adapter"]
    headers = _st["headers"]
    endpoint = _st["endpoint"]
    _sleev_route = _st["_sleev_route"]
    _connect_s = _st["_connect_s"]
    timeout = _st["timeout"]
    max_length_continuations = _st["max_length_continuations"]
    _b0 = _st["_b0"]
    length_continuations = _st["length_continuations"]
    reasoning_repair_done = _st["reasoning_repair_done"]
    tool_args_repair_done = _st["tool_args_repair_done"]
    tool_args_strip_done = _st["tool_args_strip_done"]
    api_soft_failures = _st["api_soft_failures"]
    max_api_soft_failures = _st["max_api_soft_failures"]
    force_answer_sticky = _st["force_answer_sticky"]
    force_answer_api_fail_once = _st["force_answer_api_fail_once"]
    force_answer_nudge_done = _st["force_answer_nudge_done"]
    empty_answer_retries = _st["empty_answer_retries"]
    max_empty_answer_retries = _st["max_empty_answer_retries"]
    _b1 = _st["_b1"]
    agency_rearm_count = _st["agency_rearm_count"]
    max_agency_rearms = _st["max_agency_rearms"]
    zero_tool_drive_count = _st["zero_tool_drive_count"]
    max_zero_tool_drives = _st["max_zero_tool_drives"]
    open_work_continues = _st["open_work_continues"]
    max_open_work_continues = _st["max_open_work_continues"]
    open_work_last_score = _st["open_work_last_score"]
    open_work_last_batches = _st["open_work_last_batches"]
    finish_everything_requested = _st["finish_everything_requested"]
    _st_ow = _st["_st_ow"]
    step_wall_checkpointed = _st["step_wall_checkpointed"]
    thinking_choice_repaired = _st["thinking_choice_repaired"]
    green_gate_reopen_count = _st["green_gate_reopen_count"]
    max_green_gate_reopens = _st["max_green_gate_reopens"]
    open_drive_patience = _st["open_drive_patience"]
    last_progress_score = _st["last_progress_score"]
    last_progress_step = _st["last_progress_step"]
    open_drive_stalled_notified = _st["open_drive_stalled_notified"]
    auth_refresh_done = _st["auth_refresh_done"]
    max_total = _st["max_total"]
    epoch_size = _st["epoch_size"]
    auto_continue = _st["auto_continue"]
    max_stale_epochs = _st["max_stale_epochs"]
    epoch_index = _st["epoch_index"]
    productive_in_epoch = _st["productive_in_epoch"]
    tool_batches_in_epoch = _st["tool_batches_in_epoch"]
    stale_epochs = _st["stale_epochs"]
    tool_batches_this_turn = _st["tool_batches_this_turn"]
    open_tasks_for_wall = _st["open_tasks_for_wall"]
    user_wants_stop = _st["user_wants_stop"]
    assistant_text_acc = _st["assistant_text_acc"]
    no_progress_steps = _st["no_progress_steps"]
    last_progress_fingerprint = _st["last_progress_fingerprint"]
    max_no_progress_steps = _st["max_no_progress_steps"]
    stalled_finalize = _st["stalled_finalize"]
    all_error_batches = _st["all_error_batches"]
    max_all_error_batches = _st["max_all_error_batches"]
    failed_tools_this_turn = _st["failed_tools_this_turn"]
    max_failed_tools = _st["max_failed_tools"]
    force_answer = _st["force_answer"]
    is_final_step = _st["is_final_step"]
    step_tools = _st["step_tools"]
    use_openai_sse = _st["use_openai_sse"]
    collected = _st["collected"]
    round_state = _st["round_state"]
    content_parts = _st["content_parts"]
    reasoning_parts = _st["reasoning_parts"]
    sid_mm = _st["sid_mm"]
    _verify_green = _st["_verify_green"]
    _keep_after_green = _st["_keep_after_green"]
    is_disconnect_error = _st["is_disconnect_error"]
    mid_turn_fit_messages = _st["mid_turn_fit_messages"]
    synthesize_from_tools = _st["synthesize_from_tools"]
    TurnState = _st["TurnState"]  # noqa: N806
    effective_stale_epochs = _st["effective_stale_epochs"]
    body = _st["body"]
    stream_live = _st["stream_live"]
    text_out = _st["text_out"]
    tool_calls_list = _st["tool_calls_list"]
    reasoning_out = _st["reasoning_out"]
    stutter_src = _st["stutter_src"]
    s.turn_complete = False
    # Always bind from react_turn — do not rely on bag-packed imports from
    # loop_steps (those look unused there and get dropped by autofix).
    from remedy.core.react_turn import is_disconnect_error as _ide
    from remedy.core.react_turn import mid_turn_fit_messages as _mtfm

    is_disconnect_error = _ide
    mid_turn_fit_messages = _mtfm

    def _work_unfinished() -> bool:
        return bool(helpers.work_unfinished())

    def _provider_bits() -> tuple[str, str, str]:
        return helpers.provider_bits()

    def _rearm_agency_tools() -> None:
        helpers.rearm_agency_tools()
        nonlocal tools, run_until_done
        tools = getattr(s, "tools", tools)
        run_until_done = getattr(s, "run_until_done", run_until_done)

    try:
        # Never send incomplete tool_calls/tool pairings (HTTP 400).
        # Also force valid JSON on every tool-call arguments blob before
        # POST (guards length-truncated streams + legacy history).
        repair_tool_arguments_in_messages(messages)
        messages[:] = ensure_tool_call_pairings(messages)
        # OpenAI-compatible providers (openai, deepseek, ollama, …) stream SSE.
        # Anthropic currently uses a single JSON response (stream=False).
        _bind = get_llm_binding(runtime)
        _adapter = _bind.adapter()
        body, headers, endpoint, use_openai_sse = build_step_request_body(
            runtime=runtime,
            bind=_bind,
            adapter=_adapter,
            messages=messages,
            step_tools=step_tools,
            step=int(step),
            user_message=str(message or ""),
        )
        # Sticky: a mid-SSE RST this turn must not re-enable streaming on
        # the next step (xAI TransferEncodingError / WinError 64 loop).
        if getattr(turn, "force_nonstream", False):
            if isinstance(body, dict):
                body = dict(body)
                body["stream"] = False
            use_openai_sse = False

        collected = {"content": None, "tool_calls": None}
        round_state = StreamRoundState()

        _llm_t0 = time.perf_counter()
        # Local hosts: more attempts (refit + RMB load + disconnect)
        _local_ctx_retried = False
        # Same-request 429 retries this round (Retry-After honoured).
        _rate_limit_retries = 0
        _rate_limit_waited_s = 0.0
        _http_round_ok = False
        while not _http_round_ok:
         try:
          for _local_http_attempt in range(8):
           # Hosted gateways with a declared minimum request interval
           # (catalog ``min_request_interval_s``) — space consecutive
           # rounds so a tool result does not trip the limiter.
           await _pace_before_request(
               str(getattr(_bind, "provider", "") or ""),
               _current_abort_event(),
           )
           async with http.post(
            endpoint, headers=headers, json=body, timeout=timeout
           ) as resp:
            _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
            if resp.status != 200:
                text = await resp.text()
                # Provider bodies can echo auth headers / key fragments.
                try:
                    from remedy.core.metabolism.redact import redact_text

                    safe_err = redact_text(text or "")
                except Exception:
                    safe_err = "[redacted provider error]"
                logger.error(
                    "LLM API error %d: %s", resp.status, safe_err[:500]
                )
                # Transient 429 (not quota/billing): honour Retry-After
                # and re-send the *same* request a few times before any
                # breaker / recovery / force-answer path sees it. Local
                # hosts have their own wait-and-retry path below.
                _is_local_429 = False
                with suppress(Exception):
                    from remedy.core.local_agent_optimize import (
                        is_local_binding as _ilb_429,
                    )

                    _is_local_429 = _ilb_429(
                        _bind.provider, _bind.model, _bind.base_url
                    )
                _ra_wait: float | None = None
                if (
                    _is_rate_limited(resp.status, text)
                    and not _is_local_429
                    and _rate_limit_retries < _RATE_LIMIT_MAX_RETRIES
                ):
                    # None → no Retry-After hint and no catalog
                    # interval: leave it to the breaker/recovery paths.
                    _ra_wait = _rate_limit_wait(
                        str(getattr(_bind, "provider", "") or ""),
                        getattr(resp, "headers", None),
                        text,
                    )
                if _ra_wait is not None:
                    _rate_limit_retries += 1
                    _rate_limit_waited_s += _ra_wait
                    logger.warning(
                        "HTTP 429 from %s — waiting %.1fs (retry_after) "
                        "and retrying same request (%d/%d)",
                        _bind.provider,
                        _ra_wait,
                        _rate_limit_retries,
                        _RATE_LIMIT_MAX_RETRIES,
                    )
                    yield (
                        f"@@status:Rate limited — waiting {_ra_wait:.0f}s "
                        f"and retrying ({_rate_limit_retries}/"
                        f"{_RATE_LIMIT_MAX_RETRIES})…\n"
                    )
                    # CancelledError propagates to the turn-abort handler.
                    await _sleep_abortable(_ra_wait, _current_abort_event())
                    continue  # same body, next HTTP attempt
                # Circuit breaker: 3 consecutive API/billing errors in a
                # session quarantine the provider (xAI 403 → auto-skip).
                with suppress(Exception):
                    from remedy.core.providers import (
                        provider_quarantined as _pq,
                    )
                    from remedy.core.providers import (
                        record_provider_error as _rpe,
                    )

                    _tripped = _rpe(
                        str(getattr(_bind, "provider", "") or ""),
                        resp.status,
                    )
                    if _tripped or _pq(
                        str(getattr(_bind, "provider", "") or "")
                    ):
                        yield (
                            "\n[LLM notice — provider quarantined]\n"
                            "This provider hit 3 consecutive API/billing "
                            "errors this session and is now auto-skipped. "
                            "Switch providers/models in Settings, then "
                            "resend.\n"
                        )
                        s.turn_complete = True
                        return
                # Local: auto-shrink + retry once on exceed_context_size
                if (
                    resp.status == 400
                    and not _local_ctx_retried
                    and (
                        "exceed_context" in (safe_err or "").lower()
                        or "context size" in (safe_err or "").lower()
                        or "n_prompt_tokens" in (safe_err or "").lower()
                    )
                ):
                    _local_ctx_retried = True
                    refit_ok = False
                    with suppress(Exception):
                        from remedy.core.endless_context import (
                            fit_local_request,
                            resolve_local_window,
                        )

                        win = resolve_local_window(
                            provider=_bind.provider,
                            model=_bind.model,
                            base_url=_bind.base_url,
                        )
                        hard_win = max(4096, int(win * 0.7))
                        msgs2, tools2, _meta = fit_local_request(
                            body.get("messages")
                            if isinstance(body.get("messages"), list)
                            else messages,
                            body.get("tools")
                            if isinstance(body.get("tools"), list)
                            else step_tools,
                            window=hard_win,
                            provider=_bind.provider,
                            model=_bind.model,
                            coding_bias=True,
                        )
                        body = dict(body)
                        body["messages"] = msgs2
                        if tools2:
                            body["tools"] = tools2
                            body["tool_choice"] = "required"
                        else:
                            body.pop("tools", None)
                            body.pop("tool_choice", None)
                        body["max_tokens"] = min(
                            int(body.get("max_tokens") or 512), 512
                        )
                        refit_ok = True
                        logger.warning(
                            "Local context overflow — auto-refit retry "
                            "(window~%s est=%s)",
                            hard_win,
                            _meta.get("est_after"),
                        )
                    if refit_ok:
                        yield "@@status:Context full — compressing and retrying…\n"
                        continue  # next _local_http_attempt
                with suppress(Exception):
                    from remedy.nanoswarm import get_swarm
                    from remedy.nanoswarm.events import SwarmEvent

                    get_swarm().dispatch(
                        SwarmEvent.provider_health(
                            provider=_bind.provider,
                            model=_bind.model,
                            ok=False,
                            latency_ms=_llm_ms,
                            error=safe_err[:200],
                            status_code=int(resp.status),
                        )
                    )
                # xAI (and similar): expired OAuth → refresh once, retry.
                if (
                    resp.status in (401, 403)
                    and not auth_refresh_done
                    and str(_bind.provider or "").lower() == "xai"
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
                        if new_token and new_token != _bind.api_key:
                            # Prefer turn binding; avoid racing other tabs on
                            # runtime._llm_api_key. Persist only as fallback.
                            with suppress(Exception):
                                runtime._llm_api_key = new_token
                            _bind = LlmBinding(
                                provider=_bind.provider,
                                model=_bind.model,
                                base_url=_bind.base_url,
                                api_key=new_token,
                            )
                            set_llm_binding(_bind)
                            _adapter = _bind.adapter()
                            headers = _adapter.auth_headers(_bind.api_key)
                            with suppress(Exception):
                                from remedy.core.sleev import prepare_llm_http

                                endpoint, headers = prepare_llm_http(
                                    provider=_bind.provider,
                                    base_url=_bind.base_url,
                                    api_key=_bind.api_key,
                                    adapter=_adapter,
                                    runtime=runtime,
                                )
                            logger.warning(
                                "xAI credentials refreshed after HTTP %s; retrying",
                                resp.status,
                            )
                            # Status channel only — never user answer tokens
                            # (session 2026-07-31 leaked [auth] into final content).
                            yield "@@status:Refreshed xAI session; retrying request…\n"
                            continue
                    except Exception as auth_exc:
                        logger.debug("xAI re-auth failed: %s", auth_exc)
                    # Refresh failed → clear soft-continue noise with guidance.
                    yield (
                        "@@status:xAI session expired or rejected. "
                        "Sign in again in Settings (Sign in with xAI) or "
                        "update your API key.\n"
                    )
                    # User-visible hard stop (not a monologue).
                    yield (
                        "xAI session expired or was rejected. "
                        "Sign in again under Settings (Sign in with xAI) "
                        "or update your API key, then resend."
                    )
                    s.turn_complete = True
                    return
                # DeepSeek thinking mode: tool turns require reasoning_content.
                if (
                    resp.status == 400
                    and "reasoning_content" in (text or "").lower()
                    and not reasoning_repair_done
                ):
                    reasoning_repair_done = True
                    if repair_reasoning_content_in_messages(messages):
                        logger.warning(
                            "Repaired missing reasoning_content on tool "
                            "turns; retrying request"
                        )
                        yield (
                            "@@status:Restored thinking-mode reasoning "
                            "for tool turns; continuing…\n"
                        )
                        body = dict(body) if isinstance(body, dict) else {}
                        body["messages"] = messages
                        continue
                # Truncated / invalid tool-call JSON in history (often after
                # a long plan_save or max_tokens mid-arguments stream).
                _tool_arg_err = resp.status == 400 and (
                    "tool argument" in (text or "").lower()
                    or "eof while parsing" in (text or "").lower()
                    or "invalid-argument" in (text or "").lower()
                    or "unmodified tool arguments" in (text or "").lower()
                )
                if _tool_arg_err and not tool_args_repair_done:
                    tool_args_repair_done = True
                    try:
                        repaired_n = repair_tool_arguments_in_messages(messages)
                        messages[:] = ensure_tool_call_pairings(messages)
                        logger.warning(
                            "Repaired tool-call arguments on %d assistant "
                            "turn(s) after provider 400; retrying",
                            repaired_n,
                        )
                        yield (
                            "\n[provider fix] Repaired incomplete tool "
                            "arguments; continuing…\n"
                        )
                        body = dict(body) if isinstance(body, dict) else {}
                        body["messages"] = messages
                        continue
                    except Exception as repair_exc:
                        logger.debug(
                            "tool-args repair failed: %s", repair_exc
                        )
                if _tool_arg_err and tool_args_repair_done and not tool_args_strip_done:
                    tool_args_strip_done = True
                    try:
                        stripped = strip_broken_tool_call_turns(messages)
                        messages[:] = ensure_tool_call_pairings(messages)
                        logger.warning(
                            "Stripped %d broken tool-call turn(s) after "
                            "provider 400; retrying without replaying them",
                            stripped,
                        )
                        yield (
                            "\n[provider fix] Dropped truncated tool calls "
                            "from context; continuing…\n"
                        )
                        # Prefer finishing from context rather than more tools
                        # that re-inflate huge argument payloads.
                        tools = []
                        step_tools = None
                        force_answer_sticky = True
                        body = dict(body) if isinstance(body, dict) else {}
                        body["messages"] = messages
                        body.pop("tools", None)
                        body.pop("tool_choice", None)
                        continue
                    except Exception as strip_exc:
                        logger.debug(
                            "tool-args strip failed: %s", strip_exc
                        )
                # Thinking/reasoning mode + tool_choice=required is a
                # provider class mismatch. Rebuild the body — a bare
                # continue re-POSTs the same rejected payload.
                if (
                    resp.status == 400
                    and not thinking_choice_repaired
                    and _is_thinking_tool_choice_error(text)
                ):
                    thinking_choice_repaired = True
                    set_turn_thinking_level("off")
                    set_turn_tool_choice_required_blocked(True)
                    set_turn_force_tool_choice(True)
                    logger.warning(
                        "Provider rejected tool_choice under thinking; "
                        "rebuilding without required tool_choice"
                    )
                    yield (
                        "@@status:Model cannot mix thinking with required "
                        "tools — retrying with tools…\n"
                    )
                    body, headers, endpoint, use_openai_sse = (
                        build_step_request_body(
                            runtime=runtime,
                            bind=_bind,
                            adapter=_adapter,
                            messages=messages,
                            step_tools=step_tools,
                            step=int(step),
                            user_message=str(message or ""),
                        )
                    )
                    if isinstance(body, dict):
                        body["tool_choice"] = "auto"
                    continue
                # Fatal: billing / wrong model — do not soft-retry (looks stuck).
                if _is_fatal_llm_api_error(resp.status, text):
                    model_name = str(_bind.model or "unknown")
                    prov = str(_bind.provider or "unknown")
                    if _is_billing_llm_api_error(resp.status, text):
                        yield fatal_billing_error_message(
                            status=resp.status,
                            safe_err=safe_err,
                            model_name=model_name,
                            provider=prov,
                        )
                    else:
                        yield fatal_model_error_message(
                            status=resp.status,
                            safe_err=safe_err,
                            model_name=model_name,
                            provider=prov,
                        )
                    s.turn_complete = True
                    return
                # Local RMB: 503 "Loading model" while weights load after
                # restart — wait for ready and retry same body (partner path).
                _loading = (
                    resp.status in (503, 502)
                    and (
                        "loading model" in (safe_err or "").lower()
                        or "unavailable" in (safe_err or "").lower()
                        or "not ready" in (safe_err or "").lower()
                    )
                )
                if _loading:
                    _load_waits = 0
                    with suppress(Exception):
                        from remedy.core.turn_context import _react_flags

                        _fl = _react_flags()
                        if _fl is not None:
                            _load_waits = int(_fl.rmb_load_waits or 0)
                        else:
                            _load_waits = int(
                                getattr(runtime, "_rmb_load_waits", 0) or 0
                            )
                    if _load_waits < 3:
                        with suppress(Exception):
                            from remedy.core.turn_context import _react_flags

                            _fl = _react_flags()
                            if _fl is not None:
                                _fl.rmb_load_waits = _load_waits + 1
                            else:
                                runtime._rmb_load_waits = _load_waits + 1
                        yield (
                            "@@status:Local model is loading — "
                            "waiting for RMB to become ready…\n"
                        )
                        ready = False
                        try:
                            # Poll off the event loop so we don't block
                            # other requests for 2 minutes.
                            _wr = await _wait_rmb_ready_abortable(90.0)
                            ready = bool(_wr.get("ok") and _wr.get("ready"))
                        except asyncio.CancelledError:
                            yield _stopped_note(
                                tools_executed_this_turn > 0
                                or tool_batches_this_turn > 0
                            )
                            yield "@@aborted\n"
                            s.turn_complete = True
                            return
                        except Exception:
                            pass
                        if ready:
                            yield "@@status:Model ready — retrying…\n"
                            continue  # same body, next HTTP attempt
                        # Fall through to soft recovery if still down
                from remedy.core.react_turn import soft_api_recovery_action

                # Local build: never strip tools on API blips — wait + retry
                _local_keep_tools = False
                with suppress(Exception):
                    from remedy.core.local_agent_optimize import (
                        is_local_binding as _ilb2,
                    )
                    from remedy.core.local_agent_optimize import (
                        message_wants_build_work as _mwb2,
                    )

                    _local_keep_tools = _ilb2(
                        _bind.provider, _bind.model, _bind.base_url
                    ) and (
                        _mwb2(message or "", history)
                        or tools_executed_this_turn > 0
                        or bool(all_tools)
                    )
                if _local_keep_tools and api_soft_failures < 6:
                    api_soft_failures += 1
                    yield (
                        f"@@status:Local host error HTTP {resp.status} — "
                        f"waiting and retrying with tools "
                        f"({api_soft_failures}/6)…\n"
                    )
                    try:
                        await _wait_rmb_ready_abortable(45.0)
                    except asyncio.CancelledError:
                        yield _stopped_note(
                            tools_executed_this_turn > 0
                            or tool_batches_this_turn > 0
                        )
                        yield "@@aborted\n"
                        s.turn_complete = True
                        return
                    except Exception:
                        pass
                    body = dict(body) if isinstance(body, dict) else {}
                    body["stream"] = False
                    continue

                _soft_act = soft_api_recovery_action(
                    force_answer_api_fail_once=force_answer_api_fail_once,
                    force_answer_sticky=force_answer_sticky,
                    api_soft_failures=api_soft_failures,
                    max_api_soft_failures=max_api_soft_failures,
                    keep_tools=(
                        _work_unfinished()
                        or tools_executed_this_turn > 0
                    ),
                )
                if _soft_act == "stop":
                    if force_answer_sticky and not force_answer_api_fail_once:
                        force_answer_api_fail_once = True
                    yield repeated_provider_error_message(
                        status=resp.status,
                        safe_err=safe_err,
                    )
                    if int(resp.status or 0) == 429 and _rate_limit_retries > 0:
                        yield (
                            f"(Already waited ~{_rate_limit_waited_s:.0f}s "
                            f"across {_rate_limit_retries} retries as the "
                            "provider's retry_after asked — it is still "
                            "rate-limiting.)\n"
                        )
                    s.turn_complete = True
                    return
                api_soft_failures += 1
                if _soft_act == "retry_with_tools":
                    _rearm_agency_tools()
                    force_answer_sticky = False
                    set_turn_force_tool_choice(True)
                    logger.warning(
                        "Soft API error HTTP %s — retrying with tools "
                        "(%d/%d)",
                        resp.status,
                        api_soft_failures,
                        max_api_soft_failures,
                    )
                    yield (
                        f"@@status:Provider error HTTP {resp.status} — "
                        f"retrying with tools "
                        f"({api_soft_failures}/{max_api_soft_failures})…\n"
                    )
                    continue
                # Transient path: rebuild no-tool body and POST force-answer.
                # Skip for local keep-tools (handled above).
                if _soft_act == "force_answer_rebuild" and not _local_keep_tools:
                    yield (
                        f"\n[LLM notice — HTTP {resp.status}; "
                        f"trying to finish from context "
                        f"({api_soft_failures}/{max_api_soft_failures})]\n"
                        f"{safe_err[:200]}\n"
                    )
                    tools = []
                    step_tools = None
                    force_answer_sticky = True
                    # Do NOT set force_answer_api_fail_once until this attempt fails.
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
                    try:
                        _think_r = turn_thinking_level(runtime)
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                is_local_binding as _ilb,
                            )
                            if _ilb(
                                _bind.provider, _bind.model, _bind.base_url
                            ):
                                _think_r = "low"
                        body = _adapter.build_body(
                            model=_bind.model,
                            messages=messages,
                            tools=None,
                            stream=use_openai_sse,
                            thinking_level=_think_r,
                        )
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                apply_local_body_optimize as _alo,
                            )
                            _body_lo = body if isinstance(body, dict) else {}
                            with suppress(Exception):
                                from remedy.core.turn_context import (
                                    turn_write_budget as _twb,
                                )

                                _sticky = int(_twb(runtime) or 0)
                                if _sticky > 0:
                                    _body_lo = dict(_body_lo)
                                    _body_lo["_remedy_write_budget"] = _sticky
                            body = _alo(
                                _body_lo,
                                provider=_bind.provider,
                                model=_bind.model,
                                base_url=_bind.base_url,
                                user_message=str(message or ""),
                                step_index=int(step),
                                history=messages
                                if isinstance(messages, list)
                                else None,
                            )
                        _local_agent = False
                        with suppress(Exception):
                            from remedy.runtime.rmb.mode import is_rmb_provider
                            _local_agent = is_rmb_provider(
                                _bind.provider,
                                getattr(_bind, "base_url", None),
                            ) or str(_bind.provider or "").lower() in (
                                "ollama",
                                "llamacpp",
                            )
                        body = sanitize_chat_body(
                            body if isinstance(body, dict) else {},
                            local_agent=_local_agent,
                        )
                    except Exception as _rb_exc:
                        logger.warning(
                            "force-answer body rebuild failed: %s", _rb_exc
                        )
                    continue
                if _local_keep_tools:
                    # Still building locally — never abandon tools mid-host-error
                    body = dict(body) if isinstance(body, dict) else {}
                    body["stream"] = False
                    continue
                logger.warning(
                    "LLM provider error HTTP %s: %s",
                    resp.status,
                    safe_err[:500],
                )
                _hint = {
                    401: "the model key looks invalid or expired — check it in Settings",
                    403: "the provider refused the request (key permissions or region)",
                    429: "the provider is rate-limiting or out of quota — wait a moment or switch model",
                    500: "the provider had a server error",
                    502: "the provider had a server error",
                    503: "the provider is temporarily unavailable",
                }.get(int(resp.status or 0), "the provider returned an error")
                if int(resp.status or 0) == 429 and _rate_limit_retries > 0:
                    _hint += (
                        f" (already waited ~{_rate_limit_waited_s:.0f}s "
                        f"across {_rate_limit_retries} retr"
                        f"{'y' if _rate_limit_retries == 1 else 'ies'} "
                        "as the provider asked)"
                    )
                yield (
                    f"\nThe model provider stopped responding — {_hint}. "
                    "You can switch model in Settings and resend, or try "
                    "again in a moment. Your history is intact.\n"
                )
                s.turn_complete = True
                return

            with suppress(Exception):
                from remedy.nanoswarm import get_swarm
                from remedy.nanoswarm.events import SwarmEvent

                get_swarm().dispatch(
                    SwarmEvent.provider_health(
                        provider=_bind.provider,
                        model=_bind.model,
                        ok=True,
                        latency_ms=_llm_ms,
                        status_code=200,
                    )
                )

            # Live-stream final-answer rounds (no tools this step).
            # Buffer when tools are enabled — DeepSeek-class models
            # often dump DSML tool markup as content if we stream live.
            stream_live = step_tools is None
            try:
                async for _tok, _user_flag in consume_llm_http_response(
                    resp,
                    round_state=round_state,
                    collected=collected,
                    adapter=_adapter,
                    bind=_bind,
                    body=body if isinstance(body, dict) else None,
                    use_openai_sse=use_openai_sse,
                    stream_live=stream_live,
                ):
                    if _user_flag:
                        produced_user_text = True
                    yield _tok
            except asyncio.CancelledError:
                yield _stopped_note(
                    tools_executed_this_turn > 0
                    or tool_batches_this_turn > 0
                )
                yield "@@aborted\n"
                s.turn_complete = True
                return
            from remedy.core.turn_context import is_turn_aborted as _ab_mid

            if _ab_mid():
                yield _stopped_note(
                    tools_executed_this_turn > 0
                    or tool_batches_this_turn > 0
                )
                yield "@@aborted\n"
                s.turn_complete = True
                return

            content_parts = round_state.content_parts
            reasoning_parts = round_state.reasoning_parts

           # Successful HTTP round — leave the local retry loop
           with suppress(Exception):
               from remedy.core.providers import (
                   record_provider_success as _rps,
               )

               _rps(str(getattr(_bind, "provider", "") or ""))
           _http_round_ok = True
           _log_llm(
               _bind, runtime, turn, step, _llm_t0, "ok", round_state
           )
           break
          else:
            # for-loop exhausted without break (both attempts failed status)
            yield (
                "\n[LLM ERROR] Provider request failed after retries.\n"
                "Check model/API key in Settings and try again.\n"
            )
            s.turn_complete = True
            return
         except asyncio.CancelledError:
            _log_llm(
                _bind, runtime, turn, step, _llm_t0, "aborted", round_state
            )
            yield _stopped_note(
                tools_executed_this_turn > 0 or tool_batches_this_turn > 0
            )
            yield "@@aborted\n"
            s.turn_complete = True
            return
         except Exception as _stream_exc:
          _log_llm(
              _bind, runtime, turn, step, _llm_t0, "error", round_state,
              error=_stream_exc,
          )
          _is_local_bind = False
          with suppress(Exception):
              from remedy.core.local_agent_optimize import (
                  is_local_binding as _ilb_disc,
              )

              _is_local_bind = bool(
                  _ilb_disc(
                      _bind.provider, _bind.model, _bind.base_url
                  )
              )
          from remedy.core.react_turn import is_connect_refused_error

          if is_connect_refused_error(_stream_exc) and _is_local_bind:
            # RMB / llama-server is not listening. Waiting 60s × 8 is a loop.
            yield (
                "\nI could not reach the local model — nothing is listening "
                "on the RMB port. Start RMB in Settings, or switch to a "
                "cloud model, then send again. History is intact.\n"
            )
            s.turn_complete = True
            return
          if (
            is_disconnect_error(_stream_exc)
            and turn.allow_disconnect_retry()
          ):
            turn.note_disconnect_retry()
            logger.warning(
                "LLM stream disconnect — non-stream retry (%s)",
                _stream_exc,
            )
            # Sleev dead gateway must not spin forever on RMB wait.
            # Fail-open to the real provider base URL for the rest of the turn.
            _via_sleev = False
            with suppress(Exception):
                from remedy.core.sleev import (
                    cfg_from_runtime as _sleev_cfg,
                )
                from remedy.core.sleev import (
                    is_sleev_endpoint as _is_sleev_ep,
                )

                _scfg = _sleev_cfg(runtime)
                _via_sleev = _is_sleev_ep(endpoint, _scfg)
            _exc_s = str(_stream_exc or "")
            if (
                _via_sleev
                or "17321" in _exc_s
                or (
                    "sleev" in _exc_s.lower()
                )
            ):
                with suppress(Exception):
                    from remedy.core.turn_context import (
                        set_turn_sleev_force_direct,
                    )

                    set_turn_sleev_force_direct(True, runtime)
                with suppress(Exception):
                    from remedy.core.sleev import prepare_llm_http

                    endpoint, headers = prepare_llm_http(
                        provider=_bind.provider,
                        base_url=_bind.base_url,
                        api_key=_bind.api_key,
                        adapter=_adapter,
                        runtime=runtime,
                        force_direct=True,
                    )
                yield (
                    "@@status:Sleev gateway unreachable — talking to the "
                    "provider directly (no user action needed)…\n"
                )
            else:
                if _is_local_bind:
                    yield (
                        "@@status:Connection dropped — waiting for local "
                        "model, then retrying (no user action needed)…\n"
                    )
                    # Partner: wait for RMB if mid-load only when local.
                    try:
                        await _wait_rmb_ready_abortable(60.0)
                    except asyncio.CancelledError:
                        yield _stopped_note(
                            tools_executed_this_turn > 0
                            or tool_batches_this_turn > 0
                        )
                        yield "@@aborted\n"
                        s.turn_complete = True
                        return
                    except Exception:
                        pass
                else:
                    yield (
                        "@@status:Connection dropped — retrying "
                        "(no user action needed)…\n"
                    )
            body = dict(body)
            body["stream"] = False
            with suppress(Exception):
                # Keep write-capable budget (old path forced ≤768 and
                # caused follow-on TOOL_ARGS_TRUNCATED / empty turns).
                from remedy.core.turn_context import turn_write_budget

                sticky = int(turn_write_budget(runtime) or 0)
                cur = int(body.get("max_tokens") or 2048)
                body["max_tokens"] = max(cur, sticky, 2048)
                body["max_tokens"] = min(int(body["max_tokens"]), 8192)
            prov, mod, url = _provider_bits()
            messages[:], _fit_tools = mid_turn_fit_messages(
                messages,
                body.get("tools")
                if isinstance(body.get("tools"), list)
                else step_tools,
                provider=prov,
                model=mod,
                base_url=url,
            )
            if _fit_tools is not None:
                body["tools"] = _fit_tools
            step_tools = _fit_tools if _fit_tools is not None else step_tools
            collected = {"content": None, "tool_calls": None}
            round_state = StreamRoundState()
            continue
          # Retries exhausted — always leave a durable chat explanation
          # (@@status banners alone never save to the session).
          _why = str(_stream_exc or "connection lost")[:240]
          _sleev_hint = ""
          if (
            "17321" in _why
            or "sleev" in _why.lower()
            or bool(turn_sleev_force_direct(runtime))
          ):
            _sleev_hint = (
                " The Sleev proxy looked unreachable; turn **Sleev** off "
                "in Settings → Provider until the gateway is healthy, or "
                "point it at a live local `127.0.0.1` host."
            )
          yield (
            f"\nI could not finish this turn — the model connection "
            f"failed after several retries ({_why}).{_sleev_hint} "
            "Nothing further was written here. History is intact — "
            "send **continue** or restate the request.\n"
          )
          s.turn_complete = True
          return

        # Ledger + stream usage once per LLM HTTP call (not per SSE chunk).
        _u_final = getattr(round_state, "last_usage", None)
        if isinstance(_u_final, dict) and (
            int(_u_final.get("prompt_tokens") or 0)
            or int(_u_final.get("completion_tokens") or 0)
        ):
            try:
                from remedy.core.usage import observe_provider_usage
                from remedy.core.usage_ledger import record_usage_event
                from remedy.nanoswarm.token_nanobot import get_token_nanobot

                pt = int(_u_final.get("prompt_tokens") or 0)
                ct = int(_u_final.get("completion_tokens") or 0)
                est = int(get_token_nanobot().last_estimate or 0)
                prov = _bind.provider
                mod = _bind.model
                if pt > 0 and est > 0:
                    observe_provider_usage(
                        est, pt, provider=prov, model=mod
                    )
                with suppress(Exception):
                    from remedy.core.session_quality import get_session_quality

                    get_session_quality(
                        str(
                            getattr(turn, "session_id", None)
                            or getattr(runtime, "_session_id", "")
                            or ""
                        )
                    ).record_turn(prompt_tokens=pt, completion_tokens=ct)
                with suppress(Exception):
                    record_usage_event(
                        session_id=str(
                            getattr(turn, "session_id", None)
                            or getattr(runtime, "_session_id", "")
                            or ""
                        )
                        or None,
                        provider=prov,
                        model=mod,
                        prompt_tokens=pt,
                        completion_tokens=ct,
                        total_tokens=int(
                            _u_final.get("total_tokens") or (pt + ct)
                        ),
                        estimated_cost_usd=float(
                            _u_final.get("estimated_cost_usd") or 0
                        ),
                        source=str(_u_final.get("source") or "provider"),
                        meta={
                            "cache_hit_tokens": int(
                                _u_final.get("cache_hit_tokens") or 0
                            ),
                            "cache_miss_tokens": _u_final.get(
                                "cache_miss_tokens"
                            ),
                        },
                    )
                yield (
                    "@@usage:"
                    + json.dumps(_u_final, separators=(",", ":"))
                )
            except Exception:
                pass
    finally:
        from remedy.core.react_loop.loop_bindings import pack_state
        pack_state(s, locals())
