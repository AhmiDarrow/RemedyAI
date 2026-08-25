"""ReAct LLM stream loop (orchestrator).

Package layout: ``remedy.core.react_loop`` — ``call_llm_stream`` lives here.
``loop_steps.run_react_steps`` drives the turn via ``loop_prelude`` /
``loop_http`` / ``loop_round``; small helpers are ``loop_util``. Fatal-error
helpers live in ``react_loop.errors``. Prefer importing from
``remedy.core.react_loop`` or the shim ``agent_react_loop``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import aiohttp

from remedy.core.agent_tool_batch import execute_tool_calls
from remedy.core.llm_binding import LlmBinding, get_llm_binding, set_llm_binding
from remedy.core.llm_pacing import RATE_LIMIT_MAX_RETRIES as _RATE_LIMIT_MAX_RETRIES
from remedy.core.llm_pacing import is_rate_limited as _is_rate_limited
from remedy.core.llm_pacing import pace_before_request as _pace_before_request
from remedy.core.llm_pacing import rate_limit_wait as _rate_limit_wait
from remedy.core.llm_pacing import sleep_abortable as _sleep_abortable
from remedy.core.provider_sanitize import sanitize_chat_body
from remedy.core.react_loop.binding import (
    provider_bits as _provider_bits_fn,
)
from remedy.core.react_loop.binding import (
    rearm_agency_tools as _rearm_agency_tools_fn,
)
from remedy.core.react_loop.binding import (
    resolve_and_apply_tools as _resolve_and_apply_tools_fn,
)
from remedy.core.react_loop.build_request import build_step_request_body
from remedy.core.react_loop.errors import (
    is_billing_llm_api_error as _is_billing_llm_api_error,
)
from remedy.core.react_loop.errors import (
    is_fatal_llm_api_error as _is_fatal_llm_api_error,
)
from remedy.core.react_loop.errors import (
    is_thinking_tool_choice_error as _is_thinking_tool_choice_error,
)
from remedy.core.react_loop.loop_util import browse_tool_ok as _browse_tool_ok
from remedy.core.react_loop.loop_util import log_llm_round as _log_llm
from remedy.core.react_loop.loop_util import steer_message as _steer_message
from remedy.core.react_loop.loop_util import stopped_note as _stopped_note
from remedy.core.react_loop.loop_util import take_nudges as _take_nudges
from remedy.core.react_loop.loop_util import (
    wait_rmb_ready_abortable as _wait_rmb_ready_abortable,
)
from remedy.core.react_loop.recovery import (
    fatal_billing_error_message,
    fatal_model_error_message,
    repeated_provider_error_message,
)
from remedy.core.react_loop.stream_consume import (
    _await_or_abort,
    consume_llm_http_response,
)
from remedy.core.react_loop.tool_batch import (
    apply_build_engine_after_batch,
    inject_phase_nudge,
    record_tool_batch_stats,
)
from remedy.core.react_policy import (
    TOOL_RESULT_CHAR_CAP as _TOOL_RESULT_CHAR_CAP,
)
from remedy.core.react_policy import (
    _looks_like_pseudo_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    agency_rearm_nudge_message,
    agency_tool_promise_claim,
    batch_has_approval_required,
    batch_has_empty_or_spam_write,
    batch_has_empty_search,
    batch_has_tool_errors,
    clip_appended_source_dump,
    collapse_repeated_sentences,
    epoch_continue_message,
    is_serial_explore_batch,
    looks_like_false_progress,
    looks_like_leaked_scratchpad,
    looks_like_safety_refusal,
    message_asks_to_stop,
    mission_verify_gate_message,
    post_tools_user_summary_nudge,
    recovery_nudge_message,
    speed_batch_nudge_message,
    strip_stream_status_noise,
    strip_tool_markup,
    turn_has_unfinished_work,
    unfinished_work_blocks_final,
    unfinished_work_hard_stop_message,
    unfinished_work_nudge_message,
)
from remedy.core.react_stream import (
    StreamRoundState,
    build_assistant_api_message,
    ensure_tool_call_pairings,
    filter_fresh_tool_calls,
    finalize_round_text,
    normalize_tool_calls,
    repair_reasoning_content_in_messages,
    repair_tool_arguments_in_messages,
    strip_broken_tool_call_turns,
)
from remedy.core.turn_context import current_abort_event as _current_abort_event
from remedy.core.turn_context import (
    set_turn_force_tool_choice,
    set_turn_thinking_level,
    set_turn_tool_choice_required_blocked,
    turn_max_react_steps,
    turn_sleev_force_direct,
    turn_thinking_level,
)
from remedy.core.turn_context import (
    turn_tier as _turn_tier_of,
)

logger = logging.getLogger(__name__)

# ``loop_steps.run_react_steps`` resolves names on this module at call time
# via ``bind_loop_tuple`` so tests that patch ``loop.<name>`` keep working.

_REAL_CLIENT_SESSION = aiohttp.ClientSession


@asynccontextmanager
async def _http_session(
    timeout: aiohttp.ClientTimeout,
) -> AsyncIterator[aiohttp.ClientSession]:
    """Borrow the process-wide session; never close it when the turn ends.

    Tests patch ``loop.aiohttp.ClientSession`` with fakes — honour that by
    building a per-turn instance when the class is not the real one.
    """
    if aiohttp.ClientSession is not _REAL_CLIENT_SESSION:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            yield http
        return
    from remedy.core.agent_llm import get_shared_session

    yield get_shared_session()


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
    s: Any = None
    try:
        from remedy.core.agent_react_preamble import (
            prepare_turn_preamble,
            yield_preamble_events,
        )
        from remedy.core.llm_binding import get_llm_binding

        prep = await prepare_turn_preamble(
            runtime,
            message,
            session_id,
            attachments,
            plan_mode=plan_mode,
        )
        if prep.early_reply:
            yield prep.early_reply
            return

        # Local agent bootstrap: finish clear create-jobs without waiting on
        # a 7B model that monologues instead of calling file_write.
        # Never write in Plan mode — that path skips call_tool's PLAN_MODE_BLOCKED.
        if not plan_mode:
            with suppress(Exception):
                from remedy.core.local_agent_optimize import maybe_bootstrap_local_create

                boot = await maybe_bootstrap_local_create(runtime, message or "")
                if boot:
                    yield boot
                    return
        async for _pev in yield_preamble_events(prep):
            yield _pev
        messages = prep.messages
        history = prep.history
        all_tools = prep.all_tools
        tools = prep.tools
        browse_pre_url = prep.browse.browse_pre_url
        clear_goals_only = prep.clear_goals_only
        pure_action_kick = prep.pure_action_kick
        open_only_browse = prep.browse.open_only_browse
        page_interaction = prep.browse.page_interaction

        seen_fps: set[str] = set()
        result_cache: dict[str, str] = {}
        produced_user_text = False
        pseudo_recovery_done = False
        pseudo_nudge_count = 0
        # Nudge once when the model claims progress without native tool_calls.
        false_progress_nudge_count = 0
        # After false-progress cap: still refuse intent monologues on local build/continue.
        zero_tools_hard_block_count = 0
        # Identical monologue fingerprint loop (screenshot 2026-08-08 triple repeat).
        mono_fp_last = ""
        mono_fp_hits = 0
        mono_explore_injected = False
        # Local 7B tutorial essays (RPB markdown / pip install / fenced code) — not work.
        # Reject monologue finals after a tool-heavy turn (auth/DSML recovery bug).
        scratchpad_nudge_count = 0
        tools_executed_this_turn = 0
        # One automatic recovery nudge per turn after a failing tool batch.
        recovery_nudge_done = False
        # One speed nudge if the model serializes explore as 1 tool/step.
        speed_batch_nudge_done = False
        serial_explore_streak = 0
        # Per-turn binding (parallel multi-provider); never use another turn's host/key.
        _bind = get_llm_binding(runtime)
        _adapter = _bind.adapter()
        headers = _adapter.auth_headers(_bind.api_key)
        endpoint = _adapter.chat_endpoint(_bind.base_url)
        # Sleev route (overwritten each step by build_step_request_body too).
        with suppress(Exception):
            from remedy.core.sleev import prepare_llm_http

            endpoint, headers = prepare_llm_http(
                provider=_bind.provider,
                base_url=_bind.base_url,
                api_key=_bind.api_key,
                adapter=_adapter,
                runtime=runtime,
            )

        # First-run guard: a cloud provider with no API key and no proxy/Sleev
        # route can't chat. Say so in plain language instead of a cryptic 401.
        # Local / RMB / base_url / Sleev setups never reach this branch.
        with suppress(Exception):
            from remedy.core.llm_binding import binding_looks_unconfigured

            _sleev_route = False
            with suppress(Exception):
                from remedy.core.sleev import cfg_from_runtime as _scf0
                from remedy.core.sleev import is_sleev_endpoint as _ise0

                _sleev_route = bool(_ise0(endpoint, _scf0(runtime)))
            if binding_looks_unconfigured(_bind) and not _sleev_route:
                yield (
                    "\nI don't have a model to think with yet — there's no API key "
                    f"set for **{_bind.provider or 'the provider'}**.\n\n"
                    "Open **Settings → Models**, choose a provider and paste its API "
                    "key — or switch to a local model / RMB, which needs no key. "
                    "Then send your message again.\n"
                )
                return

        # Long agent runs: high wall-clock + read idle so multi-step work
        # (and long thinking streams) are not killed mid-flight.
        # Sleev gateway: short connect so a dead proxy fails open quickly
        # instead of hanging the UI for a full minute per attempt.
        _connect_s = 60
        with suppress(Exception):
            from remedy.core.sleev import cfg_from_runtime as _scf
            from remedy.core.sleev import is_sleev_endpoint as _ise

            if _ise(endpoint, _scf(runtime)):
                _connect_s = 12
        timeout = aiohttp.ClientTimeout(
            total=3_600, sock_read=900, connect=_connect_s
        )
        # Auto-continue after finish_reason=length / max_tokens until complete.
        # No artificial short-answer wall — keep going until the model finishes.
        # Cap length continuations — unbounded 10k burned huge token budgets.
        # Local hosts: length-continue loops turn short summaries into 30k+ char
        # "Done." spam (simple C e2e). Cap hard; green path never continues.
        max_length_continuations = 16
        with suppress(Exception):
            from remedy.core.llm_binding import get_llm_binding as _glb
            from remedy.core.local_agent_optimize import is_local_binding

            _b0 = _glb(runtime)
            if is_local_binding(_b0.provider, _b0.model, _b0.base_url):
                # Extra length rounds dump more hidden thinking (R1/Qwen3).
                # Write tools still get one resume; chat/trivia do not.
                max_length_continuations = 0
        length_continuations = 0
        # Retry once after repairing DeepSeek reasoning_content on tool turns.
        reasoning_repair_done = False
        # Truncated tool-call JSON (stream cut / old sanitizer) → repair then strip.
        tool_args_repair_done = False
        tool_args_strip_done = False
        # Soft API errors: keep going when we already have tool context.
        # Low cap — fatal model errors hard-stop (see _is_fatal_llm_api_error).
        api_soft_failures = 0
        max_api_soft_failures = 3
        # Sticky force-answer after recoverable provider failures.
        force_answer_sticky = False
        # After one force-answer API attempt fails, stop (no 404 spam loop).
        force_answer_api_fail_once = False
        # Inject "Stop calling tools / final answer" user nudge at most once.
        force_answer_nudge_done = False
        # Empty-answer recovery (model thought but sent no content).
        empty_answer_retries = 0
        max_empty_answer_retries = 8
        with suppress(Exception):
            from remedy.core.llm_binding import get_llm_binding as _glb2
            from remedy.core.local_agent_optimize import is_local_binding as _ilb2

            _b1 = _glb2(runtime)
            if _ilb2(_b1.provider, _b1.model, _b1.base_url):
                max_empty_answer_retries = 1
        # Cap agency re-arms / green-gate re-opens (token burn safety).
        # Open todos / unfinished ship ignore these caps (max_total is the net).
        agency_rearm_count = 0
        max_agency_rearms = 6
        # Zero-tool work drives are a different class from promise re-arms:
        # never accept a work turn that never called a tool. Cap is only a
        # token-burn wall — then an honest stop, never a fake "I'll find…".
        zero_tool_drive_count = 0
        max_zero_tool_drives = 8
        # A final that names its own open work ("Still open: …") under a
        # finish-everything request is a hop boundary, not an answer. Bounded
        # by progress: each continuation must move the build score, else stop.
        open_work_continues = 0
        max_open_work_continues = max(
            1, int(getattr(runtime, "_max_open_work_continues", 24) or 24)
        )
        open_work_last_score = -1
        open_work_last_batches = -1
        finish_everything_requested = False
        with suppress(Exception):
            from remedy.core.react_open_work import message_asks_to_finish_everything

            finish_everything_requested = message_asks_to_finish_everything(message or "")
            if not finish_everything_requested:
                from remedy.core.build_engine import get_build_state as _gbs_ow

                _st_ow = _gbs_ow(runtime)
                finish_everything_requested = bool(
                    getattr(_st_ow, "drive_to_done", False)
                )
        #: The safety-ceiling checkpoint fires once per turn, not once per
        #: step that happens to be the last one after a re-arm.
        step_wall_checkpointed = False
        thinking_choice_repaired = False
        green_gate_reopen_count = 0
        max_green_gate_reopens = 6
        # Open-drive stall guard: open todos / unfinished ship let a build
        # override the caps above — but ONLY while it keeps advancing. Track the
        # last step that made progress; a drive that stalls for
        # `open_drive_patience` steps stops honestly instead of burning to
        # max_total. A progressing build resets the counter and is never capped.
        open_drive_patience = max(
            16, int(getattr(runtime, "_open_drive_patience", 60) or 60)
        )
        last_progress_score = 0
        last_progress_step = 0
        open_drive_stalled_notified = False

        from types import SimpleNamespace

        from remedy.core.react_loop.loop_steps import run_react_steps

        s = SimpleNamespace(
            **{
                k: v
                for k, v in locals().items()
                if k not in ('s',)
            }
        )
        async for tok in run_react_steps(s):
            yield tok
        tools_executed_this_turn = getattr(s, 'tools_executed_this_turn', 0)
        tool_batches_this_turn = getattr(s, 'tool_batches_this_turn', 0)
        produced_user_text = getattr(s, 'produced_user_text', False)
        assistant_text_acc = list(getattr(s, 'assistant_text_acc', []) or [])
        turn = getattr(s, 'turn', None)
        messages = list(getattr(s, 'messages', []) or [])
    except asyncio.CancelledError:
        yield _stopped_note(
            bool(getattr(s, "tools_executed_this_turn", 0))
            or bool(getattr(s, "tool_batches_this_turn", 0))
        )
        yield "@@aborted\n"
        return
    except Exception as e:
        logger.exception("LLM stream failed")
        # After tools: prefer synthesis over raw exception as the main answer (#4/#8)
        try:
            from remedy.core.react_turn import (
                is_disconnect_error as _is_disc,
            )
            from remedy.core.react_turn import (
                synthesize_from_tools as _synth,
            )

            _turn = getattr(s, "turn", None)
            _msgs = list(getattr(s, "messages", None) or [])
            if _turn is not None and (
                _turn.tools_executed > 0 or _turn.tool_batches > 0
            ):
                # Partner: never end the turn asking the user to resend.
                # Finish a short status ourselves; only wait on RMB when local.
                yield (
                    "@@status:Host blip after tools — recovering and finishing…\n"
                )
                _finished = False
                _err_s = str(e or "")
                _is_5xx = any(
                    tok in _err_s
                    for tok in (" 500", " 502", " 503", " 504", "status 5", "HTTP 5")
                ) or any(
                    code in _err_s
                    for code in ("502", "503", "504")
                )
                if _is_disc(e) or _is_5xx:
                    for _attempt in range(3):
                        try:
                            import asyncio as _aio

                            from remedy.core.llm_binding import get_llm_binding
                            from remedy.core.local_agent_optimize import (
                                is_local_binding as _ilb_rec,
                            )
                            from remedy.core.react_loop.stream_consume import (
                                _await_or_abort,
                            )
                            from remedy.core.turn_context import (
                                current_abort_event,
                                is_turn_aborted,
                            )

                            if is_turn_aborted():
                                break
                            _abort_ev = current_abort_event()
                            _b2 = get_llm_binding(runtime)
                            if _ilb_rec(
                                _b2.provider, _b2.model, _b2.base_url
                            ):
                                from remedy.runtime.rmb.service import wait_rmb_ready

                                _wr = await _await_or_abort(
                                    _aio.to_thread(
                                        wait_rmb_ready, None, timeout_s=90.0
                                    ),
                                    _abort_ev,
                                )
                                if is_turn_aborted():
                                    break
                                if not _wr.get("ok"):
                                    continue
                            import aiohttp as _ah

                            _ad2 = _b2.adapter()
                            _fin_msgs = list(_msgs) + [
                                {
                                    "role": "user",
                                    "content": (
                                        "Tools already ran. In ≤6 short bullets: "
                                        "what files exist / changed, and the single "
                                        "next file_write or file_edit to do. "
                                        "No essays. Prefer tool_calls if you can."
                                    ),
                                }
                            ]
                            _body2 = _ad2.build_body(
                                model=_b2.model,
                                messages=_fin_msgs,
                                tools=None,
                                stream=False,
                                thinking_level="low",
                            )
                            if isinstance(_body2, dict):
                                _body2["stream"] = False
                                _body2["max_tokens"] = min(
                                    int(_body2.get("max_tokens") or 512), 768
                                )
                            _ep = _ad2.chat_endpoint(_b2.base_url)
                            _hdr = _ad2.auth_headers(_b2.api_key)
                            with suppress(Exception):
                                from remedy.core.sleev import prepare_llm_http

                                # Prefer direct provider after any Sleev blip —
                                # recovery must not re-hit a dead proxy.
                                _ep, _hdr = prepare_llm_http(
                                    provider=_b2.provider,
                                    base_url=_b2.base_url,
                                    api_key=_b2.api_key,
                                    adapter=_ad2,
                                    runtime=runtime,
                                    force_direct=bool(
                                        turn_sleev_force_direct(runtime)
                                    )
                                    or (
                                        "17321" in str(e)
                                        or "sleev" in str(e).lower()
                                    ),
                                )
                            if is_turn_aborted():
                                break
                            async with (
                                _ah.ClientSession() as _sess,
                                _sess.post(
                                    _ep,
                                    headers=_hdr,
                                    json=_body2,
                                    timeout=_ah.ClientTimeout(total=180),
                                ) as _resp,
                            ):
                                    if is_turn_aborted():
                                        break
                                    if _resp.status == 200:
                                        _data = await _await_or_abort(
                                            _resp.json(), _abort_ev
                                        )
                                        _txt = (
                                            (
                                                (
                                                    (_data.get("choices") or [{}])[0]
                                                    .get("message")
                                                    or {}
                                                ).get("content")
                                            )
                                            or ""
                                        )
                                        if _txt.strip():
                                            yield "\n" + _txt.strip() + "\n"
                                            _finished = True
                                            break
                                    if _resp.status in (502, 503, 500, 504):
                                        continue
                        except Exception as _rec_exc:
                            logger.warning(
                                "post-tool recovery attempt %s failed: %s",
                                _attempt + 1,
                                _rec_exc,
                            )
                            continue
                if not _finished:
                    # Last resort: silent synthesis (no "say continue")
                    yield _synth(
                        _msgs,
                        paths_written=_turn.paths_written,
                    )
                    yield (
                        "\n\n@@status:Host recovered tool progress above; "
                        "continuing on next model step is automatic if you send "
                        "any short ack — history is intact.\n"
                    )
                return
        except Exception:
            pass
        # Disconnect with zero tools: explain in-chat (not status-only banners).
        from remedy.core.react_turn import is_disconnect_error as _is_disc_outer

        if _is_disc_outer(e):
            _why = str(e)[:240]
            _is_local = False
            with suppress(Exception):
                from remedy.core.llm_binding import get_llm_binding as _glb_x
                from remedy.core.local_agent_optimize import is_local_binding as _ilb_x

                _bx = _glb_x(runtime)
                _is_local = bool(
                    _ilb_x(_bx.provider, _bx.model, _bx.base_url)
                )
            if _is_local:
                try:
                    yield "@@status:Model connection lost — checking local host…\n"
                    await _wait_rmb_ready_abortable(90.0)
                except asyncio.CancelledError:
                    yield _stopped_note(
                        bool(getattr(s, "tools_executed_this_turn", 0))
                        or bool(getattr(s, "tool_batches_this_turn", 0))
                    )
                    yield "@@aborted\n"
                    return
                except Exception:
                    pass
            _sleev_note = ""
            if (
                "17321" in _why
                or "sleev" in _why.lower()
                or bool(turn_sleev_force_direct(runtime))
            ):
                _sleev_note = (
                    " If you enabled Sleev, turn it **off** in Settings until "
                    "the gateway is running again."
                )
            yield (
                f"\nConnection to the model was lost mid-turn ({_why})."
                f"{_sleev_note} "
                "No final answer was written for this step. "
                "History is intact — send **continue** to resume.\n"
            )
            return
        logger.warning("Unhandled mid-turn error: %r", e, exc_info=True)
        # Remember what actually broke — this is what self-improvement acts on.
        with suppress(Exception):
            from remedy.core.error_journal import record_exception

            record_exception(
                e, kind="turn_crash", context=str(message or "")[:200]
            )
        yield (
            "\nSomething went wrong on my side mid-turn — your history is intact.\n\n"
            "Send **continue** to pick up where we left off, or restate the request.\n"
        )
    finally:
        # Early `return` inside the step loop used to skip this — desktop
        # chat never ran soul / metabolism / speculative on a normal final.
        with suppress(Exception):
            _acc = list(getattr(s, "assistant_text_acc", None) or [])
            runtime._last_assistant_text = "".join(_acc)[-12000:]
        try:
            from remedy.core.agent_post_turn import schedule_post_turn_prep

            schedule_post_turn_prep(
                runtime,
                message=message or "",
                session_id=session_id,
            )
        except Exception as _ptp_exc:
            # Best-effort (the reply already streamed) — but log so a failing
            # memory/soul save is diagnosable instead of vanishing silently.
            logger.warning("post-turn prep failed: %r", _ptp_exc)
            with suppress(Exception):
                from remedy.core.error_journal import record_exception

                record_exception(_ptp_exc, kind="post_turn", context="post-turn prep")
        # Body coordination: a turn that ends with the build green/done (or that
        # never wrote) has no unfinished work to protect — free its file holds
        # so a sibling can take over immediately (live handoff). An UNFINISHED
        # build (red verify / open todos) keeps its claims across turns.
        with suppress(Exception):
            from remedy.core.build_engine import (
                build_has_open_drive,
                get_build_state,
            )
            from remedy.core.coordination import release_path

            _bst_f = get_build_state(runtime)
            _done_enough = (
                _bst_f is None
                or not getattr(_bst_f, "active", False)
                or (
                    not build_has_open_drive(_bst_f)
                    and (
                        getattr(_bst_f, "last_verify_ok", None) is True
                        or int(getattr(_bst_f, "write_steps", 0) or 0) == 0
                        or getattr(_bst_f, "phase", "") == "done"
                    )
                )
            )
            if _done_enough and session_id:
                release_path(str(session_id), None)


