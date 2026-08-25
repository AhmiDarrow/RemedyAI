"""Session API routes package."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from remedy.interfaces.api_models import (
    SendMessageRequest,
)
from remedy.interfaces.api_support import (
    _sse_stream_text,
    load_config,
    sse_headers,
)
from remedy.interfaces.attachments import filter_jailed_attachments
from remedy.interfaces.routes.sessions.stream_tokens import (
    parse_tool_call_token,
    sse_event,
)
from remedy.models import (
    ChatMessageRole,
)

logger = logging.getLogger(__name__)



# Strong refs for detached persist tasks: a dying SSE generator spawns the DB
# write as its own task so a closed client connection cannot drop the row.
_PERSIST_TASKS: set[asyncio.Task] = set()

STOP_NOTE = "*(Generation stopped. History is intact — send **continue** to resume.)*"
SUPERSEDE_NOTE = (
    "*(Interrupted by your next message — partial work above is saved. "
    "Say **continue** to pick it up.)*"
)
# Comment-frame pings keep WebView2 / fetch from dropping a silent SSE while
# host_run / the LLM is blocked. Session 765c: idle gaps aborted the turn as
# "Generation stopped". SSE comments are ignored by the desktop parser.
SSE_KEEPALIVE_INTERVAL_S = 12.0
SSE_KEEPALIVE_COMMENT = ": keepalive\n\n"


async def iter_sse_with_keepalive(
    agen,
    *,
    interval_s: float | None = None,
):
    """Yield SSE comment pings while *agen* is blocked on LLM/tools."""
    interval = float(
        SSE_KEEPALIVE_INTERVAL_S if interval_s is None else interval_s
    )
    if interval <= 0:
        async for chunk in agen:
            yield chunk
        return
    it = agen.__aiter__()
    pending = asyncio.ensure_future(anext(it))
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    asyncio.shield(pending), timeout=interval
                )
            except TimeoutError:
                yield SSE_KEEPALIVE_COMMENT
                continue
            except StopAsyncIteration:
                return
            yield chunk
            pending = asyncio.ensure_future(anext(it))
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending


def interrupted_turn_note(reason: str | None) -> str:
    """Durable-row footer for an interrupted turn, worded by abort reason."""
    return SUPERSEDE_NOTE if str(reason or "").strip().lower() == "supersede" else STOP_NOTE


def interrupted_turn_content(partial: str, reason: str | None, *, has_tools: bool) -> str:
    """Partial text (if any) + the reason note. Tool-only turns keep a marker line."""
    body = (partial or "").strip()
    if body:
        with contextlib.suppress(Exception):
            from remedy.core.react_policy import collapse_repeated_sentences

            body = collapse_repeated_sentences(body)
    note = interrupted_turn_note(reason)
    if not body:
        return f"*(Used tools — see process.)*\n\n{note}" if has_tools else note
    return f"{body}\n\n{note}"


def register_stream_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register stream session routes."""
    _ = gateway  # may be unused in some modules
    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(
        session_id: str,
        req: SendMessageRequest,
        x_remedy_session_select: str | None = Header(default=None),
    ):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")
        _sel = str(x_remedy_session_select or "").strip().lower()
        owner_selected = _sel in ("1", "true", "yes", "on")

        # Serialize same-session streams (multi-tab / double-submit safety).
        # Claim *before* persisting the user message so two POSTs cannot both start.
        from remedy.core.turn_context import (
            release_session_stream_claim,
            session_reset_epoch,
            stream_claim_epoch,
            try_claim_session_stream,
        )

        if not try_claim_session_stream(session_id):
            raise HTTPException(
                409,
                "Session already has a generation in progress. "
                "Stop the current turn first, then send again.",
            )
        claim_epoch = stream_claim_epoch(session_id)
        reset_epoch_at_start = session_reset_epoch(session_id)

        # An EXPLICIT provider choice on this send gives that provider a fresh
        # chance: the user may have just fixed billing/keys ("I added credits").
        # Without this, a quarantined provider stayed dead until process restart
        # (clear_provider_quarantine had no callers). Three consecutive failures
        # re-quarantine immediately, so a genuinely broken provider stays cheap.
        if (req.provider or "").strip():
            with contextlib.suppress(Exception):
                from remedy.core.providers import clear_provider_quarantine

                clear_provider_quarantine(req.provider)

        # CancelledError is BaseException — a bare `except Exception` leaked the
        # claim and 409'd the session until process restart.
        handed_to_stream = False
        try:
            request_id = str(uuid4())
            att_dicts = [a.model_dump() for a in (req.attachments or [])]
            # Path jail: drop client-forged paths outside attachments tree before
            # any snippet inject / multimodal / vision decode.
            home_att = None
            with contextlib.suppress(Exception):
                home_att = load_config().get("home_dir")
            # Module-level import on purpose: inside the suppress, an import
            # failure left every attachment unfiltered. A runtime failure
            # drops them all — closed, not open.
            try:
                att_dicts = filter_jailed_attachments(
                    att_dicts, home_dir=home_att, session_id=session_id
                )
            except Exception:
                logger.exception("attachment path jail failed; dropping attachments")
                att_dicts = []
            user_text = (req.message or "").strip()
            if not user_text and not att_dicts:
                raise HTTPException(400, "Message or attachment required")

            # Expand display content for chat history (paths + text snippets).
            from remedy.interfaces.attachments import (
                build_attachment_prompt_block,
                inject_text_file_snippets,
            )

            display_content = user_text
            if att_dicts:
                _block = build_attachment_prompt_block(
                    att_dicts, home_dir=home_att
                )
                display_content = (
                    f"{user_text}{_block}" if user_text else _block.lstrip()
                )
                # Keep history readable but not huge — skip full snippets for images-only.
                if any(a.get("is_text") for a in att_dicts):
                    display_content = display_content + inject_text_file_snippets(
                        att_dicts, home_dir=home_att, session_id=session_id
                    )

            if memory:
                from remedy.core.session_llm import (
                    resolve_session_llm_bind,
                    session_llm_update_fields,
                )
                from remedy.interfaces.routes.sessions.titles import (
                    looks_like_path_title as _looks_like_path_title,
                )
                from remedy.interfaces.routes.sessions.titles import (
                    title_from_prompt as _title_from_prompt,
                )
                from remedy.models import ChatMessage

                existing = await memory.get_chat_session(session_id)
                # Persist the tab bind — never the process-wide live GGUF stem.
                sp, sm = resolve_session_llm_bind(
                    session=existing,
                    req_provider=getattr(req, "provider", None),
                    req_model=req.model,
                    use_live_rmb=False,
                )
                fields = session_llm_update_fields(provider=sp, model=sm)
                if existing is None:
                    raise HTTPException(404, "Session not found")
                else:
                    # Auto-name placeholder sessions from the first real prompt.
                    # Also replace path-only titles (e.g. full OneDrive screenshot path)
                    # when the user later sends a real message or plan prompt.
                    cur_title = (existing.title or "").strip()
                    placeholder = (
                        not cur_title
                        or cur_title.lower()
                        in (
                            "new session",
                            "new chat",
                            "untitled",
                            "attachments",
                            "attachment",
                            "image",
                            "screenshot",
                        )
                        or _looks_like_path_title(cur_title)
                    )
                    from remedy.interfaces.routes.sessions.titles import (
                        should_refresh_living_title as _should_refresh_living_title,
                    )

                    if placeholder and (user_text or att_dicts):
                        new_title = _title_from_prompt(
                            user_text
                            or str(
                                att_dicts[0].get("name") if att_dicts else "Attachments"
                            ),
                            att_dicts=att_dicts,
                        )
                        # Prefer real user text over another path-ish attachment name
                        if user_text.strip() and not _looks_like_path_title(new_title):
                            await memory.update_chat_session(
                                session_id, title=new_title
                            )
                        elif placeholder and (
                            _looks_like_path_title(cur_title) or not cur_title
                        ):
                            await memory.update_chat_session(
                                session_id, title=new_title
                            )
                    elif user_text.strip() and _should_refresh_living_title(
                        cur_title, user_text
                    ):
                        # Endless session: one thread, sidebar name follows this beat.
                        new_title = _title_from_prompt(
                            user_text, att_dicts=att_dicts
                        )
                        if new_title and not _looks_like_path_title(new_title):
                            await memory.update_chat_session(
                                session_id, title=new_title
                            )
                    # Persist paired bind — never model without provider (cross-tab corruption).
                    if fields and (
                        fields.get("model") != existing.model
                        or fields.get("llm_provider") != getattr(existing, "llm_provider", None)
                    ):
                        await memory.update_chat_session(session_id, **fields)

                await memory.add_chat_message(ChatMessage(
                    session_id=session_id,
                    role=ChatMessageRole.USER,
                    content=display_content,
                    model=req.model,
                    agent=req.agent,
                ))

            # Sticky per-session provider+model (multi-tab multi-provider).
            from remedy.core.session_llm import resolve_session_llm_bind

            sess_provider = getattr(req, "provider", None)
            sess_model = req.model
            if memory:
                with contextlib.suppress(Exception):
                    ex = await memory.get_chat_session(session_id)
                    sess_provider, sess_model = resolve_session_llm_bind(
                        session=ex,
                        req_provider=getattr(req, "provider", None),
                        req_model=req.model,
                    )

            # Credentials stay on the per-turn binding — do not write the
            # process-wide runtime (sibling sessions keep their own provider).
            api_key = ""
            with contextlib.suppress(Exception):
                from remedy.interfaces.api_support import resolve_llm_slot

                _p, _m, _u, api_key = resolve_llm_slot(
                    provider_override=sess_provider,
                    model_override=sess_model,
                )

            async def event_stream():
                from remedy.core.turn_context import (
                    drop_turn_job,
                    register_turn_job,
                )

                q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=512)

                async def _job() -> None:
                    try:
                        async for chunk in _event_stream_body():
                            try:
                                q.put_nowait(chunk)
                            except asyncio.QueueFull:
                                with contextlib.suppress(Exception):
                                    q.get_nowait()
                                await q.put(chunk)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("detached turn job failed")
                    finally:
                        with contextlib.suppress(Exception):
                            await q.put(None)
                        release_session_stream_claim(
                            session_id, epoch=claim_epoch
                        )
                        drop_turn_job(session_id)

                job = asyncio.create_task(_job())
                register_turn_job(session_id, job)

                async def _subscribe():
                    while True:
                        item = await q.get()
                        if item is None:
                            return
                        yield item

                try:
                    async for _chunk in iter_sse_with_keepalive(_subscribe()):
                        yield _chunk
                except asyncio.CancelledError:
                    # Socket died. Stop is POST /abort (cancels the job).
                    return

            async def _event_stream_body():
                from remedy.core.metrics import default_registry
                from remedy.core.session_continuity import (
                    reset_owner_session_select,
                    set_owner_session_select,
                )

                select_tok = set_owner_session_select(owner_selected)
                t0 = time.perf_counter()
                status = "ok"
                yield (
                    f"event: start\ndata: {json.dumps({'type': 'start', 'request_id': request_id, 'session_id': session_id})}\n\n"
                )

                full_response = ""
                full_thinking = ""
                thinking_replace_next = False
                collected_tool_calls: list[dict] = []
                collected_tool_results: list[dict] = []
                usage_acc: dict | None = None
                # Always enter stream_response: L0 (list skills / model / version /
                # whoami / status) works without a provider key. Non-L0 without a
                # key still gets a clear notice from the agent (streamed as text).
                aborted = False
                persist_done = False
                body_finished = False

                async def _write_interrupted_row(
                    content: str, thinking, calls, results
                ) -> None:
                    """Detached DB write — runs to completion even when the SSE
                    generator that spawned it is already closed."""
                    with contextlib.suppress(Exception):
                        if session_reset_epoch(session_id) != reset_epoch_at_start:
                            return
                        still = await memory.get_chat_session(session_id)
                        if still is None:
                            return
                        await memory.add_chat_message(
                            ChatMessage(
                                session_id=session_id,
                                role=ChatMessageRole.ASSISTANT,
                                content=content,
                                thinking=thinking,
                                tool_calls=calls,
                                tool_results=results,
                                model=sess_model
                                or req.model
                                or getattr(runtime, "_llm_model", None),
                            )
                        )

                def _persist_interrupted(kind: str) -> asyncio.Task | None:
                    """Always leave a durable assistant row for an interrupted
                    turn (Stop, superseding message, disconnect, generator close).
                    Snapshots state synchronously and spawns the write as its own
                    task so it survives the connection going away."""
                    nonlocal persist_done
                    if persist_done or memory is None:
                        return None
                    persist_done = True
                    reason = None
                    with contextlib.suppress(Exception):
                        from remedy.core.turn_context import peek_abort_reason

                        reason = peek_abort_reason(session_id)
                    calls = list(collected_tool_calls)
                    results = list(collected_tool_results)
                    content = interrupted_turn_content(
                        full_response, reason, has_tools=bool(calls or results)
                    )
                    thinking = (full_thinking or "").strip() or None
                    logger.info(
                        "stream %s persisting interrupted turn session=%s reason=%s "
                        "text=%d tool_calls=%d",
                        kind,
                        session_id,
                        reason or "stop",
                        len((full_response or "").strip()),
                        len(calls),
                    )
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        return None
                    task = loop.create_task(
                        _write_interrupted_row(content, thinking, calls, results)
                    )
                    _PERSIST_TASKS.add(task)
                    task.add_done_callback(_PERSIST_TASKS.discard)
                    return task

                try:
                    with contextlib.suppress(Exception):
                        from remedy.core.build_todos import begin_chat_beat

                        begin_chat_beat(runtime, session_id)
                    async for token in runtime.stream_response(
                        user_text or "(see attached files)",
                        session_id=session_id,
                        model=sess_model,
                        attachments=att_dicts,
                        plan_mode=bool(getattr(req, "plan_mode", False)),
                        provider=sess_provider,
                    ):
                        if isinstance(token, str) and token.startswith("@@aborted"):
                            status = "aborted"
                            aborted = True
                            # Durable row FIRST: the client that called /abort may
                            # already have dropped this connection, in which case
                            # the yield below raises GeneratorExit.
                            _t = _persist_interrupted("aborted")
                            if _t is not None:
                                with contextlib.suppress(BaseException):
                                    await asyncio.shield(_t)
                            # Cooperative stop — not an error. Client Stop / abort.
                            yield (
                                "event: aborted\ndata: "
                                + json.dumps(
                                    {
                                        "type": "aborted",
                                        "message": "Generation stopped",
                                        "request_id": request_id,
                                    }
                                )
                                + "\n\n"
                            )
                            break
                        if token.startswith("@@tool_call:"):
                            parsed = parse_tool_call_token(token)
                            collected_tool_calls.append(parsed)
                            yield sse_event(
                                "tool_call",
                                {
                                    "type": "tool_call",
                                    "name": parsed["name"],
                                    "args": parsed.get("args") or {},
                                },
                            )
                        elif token.startswith("@@tool_result:"):
                            raw = token[len("@@tool_result:") :]
                            tool_name = raw
                            preview = ""
                            ok = True
                            try:
                                if raw.strip().startswith("{"):
                                    obj = json.loads(raw)
                                    tool_name = str(obj.get("name") or "tool")
                                    preview = str(obj.get("preview") or "")
                                    ok = bool(obj.get("ok", True))
                                else:
                                    tool_name = raw.split("|", 1)[0].strip() or "tool"
                            except Exception:
                                tool_name = raw.split("|", 1)[0].strip() or "tool"
                            collected_tool_results.append(
                                {
                                    "name": tool_name,
                                    "output": preview,
                                    "error": None if ok else (preview or "tool failed"),
                                }
                            )
                            yield (
                                "event: tool_result\ndata: "
                                + json.dumps(
                                    {
                                        "type": "tool_result",
                                        "name": tool_name,
                                        "preview": preview,
                                        "ok": ok,
                                    },
                                    default=str,
                                )
                                + "\n\n"
                            )
                        elif token.startswith("@@library_suggest:"):
                            raw = token[len("@@library_suggest:") :].strip()
                            try:
                                payload = json.loads(raw)
                            except Exception:
                                payload = {}
                            if isinstance(payload, dict) and payload.get("id"):
                                yield (
                                    "event: library_suggest\ndata: "
                                    + json.dumps(
                                        {"type": "library_suggest", **payload},
                                        default=str,
                                    )
                                    + "\n\n"
                                )
                        elif token.startswith("@@progress:"):
                            # Generic task/job progress for the desktop progress bar.
                            raw = token[len("@@progress:") :]
                            try:
                                payload = json.loads(raw) if raw else {}
                            except Exception:
                                payload = {"label": raw or "Working…"}
                            if not isinstance(payload, dict):
                                payload = {"label": str(payload)}
                            event = {"type": "progress", **payload}
                            yield f"event: progress\ndata: {json.dumps(event)}\n\n"
                        elif token.startswith("@@status:"):
                            # Vision decode / mid-turn status — never chat body.
                            label = token[len("@@status:") :].strip()
                            if label:
                                yield (
                                    "event: progress\ndata: "
                                    + json.dumps(
                                        {
                                            "type": "progress",
                                            "label": label,
                                        },
                                        default=str,
                                    )
                                    + "\n\n"
                                )
                        elif token.startswith("@@steered"):
                            # The owner's mid-turn words were folded in.
                            yield sse_event(
                                "progress",
                                {"type": "progress", "label": "Taking that in…"},
                            )
                        elif token.startswith("@@thinking_round"):
                            # Next thinking tokens belong to a new model round.
                            # Replace (don't stack) when the first token arrives
                            # so tool-time still shows the last scratchpad.
                            thinking_replace_next = True
                        elif token.startswith("@@thinking:"):
                            thought = token[len("@@thinking:") :]
                            if thought:
                                if thinking_replace_next:
                                    thinking_replace_next = False
                                    full_thinking = thought
                                    yield sse_event(
                                        "thinking",
                                        {
                                            "type": "thinking",
                                            "text": thought,
                                            "replace": True,
                                        },
                                    )
                                else:
                                    full_thinking += thought
                                    yield await _sse_stream_text(
                                        thought, event="thinking"
                                    )
                        elif token.startswith("@@usage:"):
                            raw_u = token[len("@@usage:") :]
                            try:
                                from remedy.core.usage import merge_usage

                                part = json.loads(raw_u) if raw_u else {}
                                if isinstance(part, dict):
                                    usage_acc = merge_usage(usage_acc, part)
                                    yield (
                                        "event: usage\ndata: "
                                        + json.dumps({"type": "usage", **usage_acc})
                                        + "\n\n"
                                    )
                            except Exception:
                                pass
                        elif token.startswith("@@image_markdown:"):
                            # ComfyUI (etc.): image markdown (path/URL ref) — show immediately.
                            md = token[len("@@image_markdown:"):]
                            if md:
                                yield await _sse_stream_text(md, event="token")
                                # Never let a data-URI into persisted/provider history.
                                from remedy.memory.inline_images import strip_inline_images

                                full_response += ("\n\n" if full_response else "") + strip_inline_images(md)
                        elif token == "@@tool_calls":
                            pass
                        elif isinstance(token, str) and token.startswith("@@todos:"):
                            raw = token[len("@@todos:") :]
                            try:
                                payload = json.loads(raw) if raw else {}
                            except Exception:
                                payload = {}
                            if isinstance(payload, dict):
                                yield sse_event(
                                    "todos",
                                    {"type": "todos", **payload},
                                )
                        elif isinstance(token, str) and token.startswith("@@"):
                            # Unknown control token — never leak into the bubble.
                            logger.debug(
                                "Skipping unknown stream control token: %s", token[:80]
                            )
                            continue
                        else:
                            # Never stream DSML / fake tool markup into the chat bubble.
                            from remedy.core.react_policy import (
                                looks_like_pseudo_tools,
                                strip_tool_markup,
                            )

                            if looks_like_pseudo_tools(token):
                                cleaned = strip_tool_markup(token)
                                if not cleaned:
                                    continue
                                token = cleaned
                            full_response += token
                            yield await _sse_stream_text(token, event="token")

                    # Metrics: no provider key notice from agent (L0 never hits this).
                    if (
                        not aborted
                        and status == "ok"
                        and not api_key
                        and full_response
                        and "no API key" in full_response
                    ):
                        status = "no_key"

                    # Final usage: prefer provider totals; fall back to char estimate.
                    final_usage: dict | None = None
                    try:
                        from remedy.core.usage import estimate_turn_usage, merge_usage

                        est = estimate_turn_usage(
                            user_text=user_text or "",
                            assistant_text=full_response or "",
                            thinking_text=full_thinking or "",
                            model=sess_model or req.model,
                            provider=sess_provider,
                        )
                        if usage_acc and usage_acc.get("source") == "provider":
                            # Provider totals already sum each LLM round; never add
                            # char-heuristic estimate on top (that inflated costs).
                            final_usage = merge_usage(usage_acc)
                        elif usage_acc:
                            final_usage = merge_usage(usage_acc)
                        else:
                            final_usage = merge_usage(est)
                        if not aborted and isinstance(final_usage, dict):
                            yield (
                                "event: usage\ndata: "
                                + json.dumps({"type": "usage", **final_usage})
                                + "\n\n"
                            )
                    except Exception:
                        final_usage = None

                    # Never persist leaked DSML / "tool_c" as the chat bubble.
                    if full_response:
                        with contextlib.suppress(Exception):
                            from remedy.core.react_policy import (
                                looks_like_pseudo_tools,
                                looks_like_tool_markup_prefix,
                                strip_tool_markup,
                            )

                            if looks_like_pseudo_tools(full_response) or looks_like_tool_markup_prefix(
                                full_response
                            ):
                                full_response = strip_tool_markup(full_response)

                    # Always leave a durable assistant row when we have any text
                    # (including stop/disconnect explanations). Status-only turns
                    # used to vanish with no chat bubble.
                    if aborted and not (full_response or "").strip():
                        full_response = (
                            "*(Generation stopped. History is intact — "
                            "send **continue** to resume.)*"
                        )

                    has_tools = bool(collected_tool_calls or collected_tool_results)
                    persist_text = (full_response or "").strip()
                    if persist_text:
                        with contextlib.suppress(Exception):
                            from remedy.core.react_policy import (
                                collapse_repeated_sentences,
                            )

                            persist_text = collapse_repeated_sentences(persist_text)
                    if not persist_text and has_tools:
                        persist_text = "*(Used tools — see process.)*"

                    if persist_text and memory and not persist_done:
                        if session_reset_epoch(session_id) != reset_epoch_at_start:
                            persist_done = True
                            still = None
                        else:
                            still = await memory.get_chat_session(session_id)
                        if still is None:
                            persist_done = True
                        else:
                            tok = None
                            if isinstance(final_usage, dict):
                                tok = int(final_usage.get("total_tokens") or 0) or None
                            think_persist = (full_thinking or "").strip() or None
                            with contextlib.suppress(Exception):
                                from remedy.core.react_policy import (
                                    collapse_repeated_sentences,
                                    looks_like_policy_thinking,
                                )

                                if think_persist:
                                    think_persist = collapse_repeated_sentences(
                                        think_persist
                                    )
                                if think_persist and looks_like_policy_thinking(
                                    think_persist
                                ):
                                    think_persist = None
                            await memory.add_chat_message(ChatMessage(
                                session_id=session_id,
                                role=ChatMessageRole.ASSISTANT,
                                content=persist_text,
                                thinking=think_persist,
                                tool_calls=collected_tool_calls,
                                tool_results=collected_tool_results,
                                model=sess_model
                                or req.model
                                or getattr(runtime, "_llm_model", None),
                                tokens=tok,
                            ))
                            persist_done = True
                        # Desktop→messenger: mirror so Telegram users see desktop replies.
                        with contextlib.suppress(Exception):
                            from remedy.gateway.session_bridge import (
                                mirror_desktop_reply_to_messenger,
                            )
                            from remedy.interfaces.session_events import publish_session_event

                            ex = await memory.get_chat_session(session_id)
                            if ex is not None:
                                await mirror_desktop_reply_to_messenger(
                                    gateway, ex, persist_text
                                )
                                if getattr(ex, "origin_channel", None):
                                    await publish_session_event(
                                        "message_added",
                                        session_id,
                                        origin_channel=getattr(ex, "origin_channel", None),
                                        title=getattr(ex, "title", None),
                                        role="assistant",
                                    )

                    # Flush the checklist event a last-batch verify queued
                    # after that batch's flush point — otherwise a build that
                    # finishes on its final tool batch leaves the stale
                    # checklist on screen until the next turn.
                    with contextlib.suppress(Exception):
                        from remedy.core.build_todos import take_todos_event

                        _td_tok = take_todos_event(runtime, session_id=session_id)
                        if _td_tok:
                            _td_raw = _td_tok[len("@@todos:") :]
                            _td_payload = json.loads(_td_raw) if _td_raw else {}
                            if isinstance(_td_payload, dict):
                                yield sse_event(
                                    "todos", {"type": "todos", **_td_payload}
                                )

                    done_payload: dict = {
                        "type": "done",
                        "request_id": request_id,
                        "status": "aborted" if aborted else "ok",
                    }
                    if isinstance(final_usage, dict) and not aborted:
                        done_payload["usage"] = final_usage
                    body_finished = True
                    yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

                except asyncio.CancelledError:
                    # Client disconnect / ASGI cancel — kill shell children + CUA jobs
                    # so Stop / tab close does not leave tools running.
                    status = "cancelled"
                    # CPython 3.12 sticky cancellation: the next await would
                    # re-raise unless we uncancel first. Persist + abort must
                    # still run so Stop does not drop the assistant row or
                    # leave tools running.
                    _task = asyncio.current_task()
                    if _task is not None:
                        with contextlib.suppress(Exception):
                            _task.uncancel()
                    # Persist whatever we already streamed. Fetch abort used to
                    # drop the assistant row so reload showed only the user turn.
                    # Detached + shielded: a second cancel cannot kill the write.
                    _t = _persist_interrupted("cancelled")
                    if _t is not None:
                        with contextlib.suppress(BaseException):
                            await asyncio.shield(_t)
                    with contextlib.suppress(Exception):
                        from remedy.core.turn_context import abort_session as _abort_turn

                        _abort_turn(session_id, epoch=claim_epoch)
                    if runtime is not None:
                        with contextlib.suppress(Exception):
                            ss = getattr(runtime, "_streaming_sessions", None)
                            if isinstance(ss, set):
                                ss.discard(str(session_id))
                    raise
                except Exception as e:
                    status = "error"
                    body_finished = True
                    persist_done = True
                    logger.exception("SSE stream error")
                    # Never stream raw exception text — may embed keys / tokens.
                    try:
                        from remedy.core.metabolism.redact import redact_text

                        safe_msg = redact_text(str(e))[:800]
                    except Exception:
                        safe_msg = "Stream error (details redacted)"
                    if not safe_msg.strip():
                        safe_msg = "Stream error"
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': safe_msg})}\n\n"
                    # Persist a short explanation so the chat is not empty after a crash.
                    with contextlib.suppress(Exception):
                        if (
                            memory is not None
                            and session_reset_epoch(session_id) == reset_epoch_at_start
                        ):
                            still = await memory.get_chat_session(session_id)
                            if still is not None:
                                note = (
                                    full_response.strip() + "\n\n"
                                    if (full_response or "").strip()
                                    else ""
                                )
                                note += (
                                    f"*(Turn ended with an error: {safe_msg}. "
                                    "History is intact — send **continue** to resume.)*"
                                )
                                await memory.add_chat_message(
                                    ChatMessage(
                                        session_id=session_id,
                                        role=ChatMessageRole.ASSISTANT,
                                        content=note,
                                        model=sess_model
                                        or req.model
                                        or getattr(runtime, "_llm_model", None),
                                    )
                                )
                finally:
                    reset_owner_session_select(select_tok)
                    # GeneratorExit (client closed the SSE mid-yield) skips every
                    # except-branch above. Never lose partial text / tool calls.
                    if not body_finished and not persist_done and (
                        aborted
                        or (full_response or "").strip()
                        or collected_tool_calls
                        or collected_tool_results
                    ):
                        with contextlib.suppress(Exception):
                            _persist_interrupted("closed")
                    default_registry.counter(
                        "remedy_chat_requests_total", path="session_stream", status=status
                    ).inc()
                    default_registry.histogram(
                        "remedy_chat_duration_seconds", path="session_stream"
                    ).observe(time.perf_counter() - t0)

            handed_to_stream = True
            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers=sse_headers(),
            )
        except BaseException:
            if not handed_to_stream:
                release_session_stream_claim(session_id, epoch=claim_epoch)
            raise
