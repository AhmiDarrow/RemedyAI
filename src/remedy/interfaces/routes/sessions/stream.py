"""Session API routes package."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from remedy.interfaces.api_models import (
    SendMessageRequest,
)
from remedy.interfaces.api_support import (
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    load_config,
    sse_headers,
)
from remedy.interfaces.routes.sessions.stream_tokens import (
    parse_tool_call_token,
    sse_event,
)
from remedy.models import (
    ChatMessageRole,
)

logger = logging.getLogger(__name__)



def register_stream_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register stream session routes."""
    _ = gateway  # may be unused in some modules
    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(session_id: str, req: SendMessageRequest):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")

        # Serialize same-session streams (multi-tab / double-submit safety).
        # Claim *before* persisting the user message so two POSTs cannot both start.
        from remedy.core.turn_context import (
            release_session_stream_claim,
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
            with contextlib.suppress(Exception):
                from remedy.interfaces.attachments import filter_jailed_attachments

                att_dicts = filter_jailed_attachments(
                    att_dicts, home_dir=home_att, session_id=session_id
                )
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
                from remedy.models import ChatMessage, ChatSession

                existing = await memory.get_chat_session(session_id)
                sp, sm = resolve_session_llm_bind(
                    session=existing,
                    req_provider=getattr(req, "provider", None),
                    req_model=req.model,
                )
                fields = session_llm_update_fields(provider=sp, model=sm)
                if existing is None:
                    default_proj = load_config().get("project_path")
                    title_src = user_text or (
                        att_dicts[0].get("name") if att_dicts else "Attachments"
                    )
                    await memory.create_chat_session(ChatSession(
                        id=session_id,
                        title=_title_from_prompt(str(title_src), att_dicts=att_dicts),
                        model=fields.get("model") or req.model,
                        llm_provider=fields.get("llm_provider"),
                        agent=req.agent,
                        project_path=default_proj,
                    ))
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

            # Always re-sync credentials from disk (first-run wizard / settings).
            api_key = _sync_runtime_llm_from_config(
                runtime,
                model_override=sess_model,
                provider_override=sess_provider,
                llm_only=True,
            )

            async def event_stream():
                from remedy.core.metrics import default_registry

                t0 = time.perf_counter()
                status = "ok"
                try:
                    async for _chunk in _event_stream_body():
                        yield _chunk
                finally:
                    release_session_stream_claim(session_id, epoch=claim_epoch)

            async def _event_stream_body():
                from remedy.core.metrics import default_registry

                t0 = time.perf_counter()
                status = "ok"
                yield (
                    f"event: start\ndata: {json.dumps({'type': 'start', 'request_id': request_id, 'session_id': session_id})}\n\n"
                )

                try:
                    full_response = ""
                    full_thinking = ""
                    collected_tool_calls: list[dict] = []
                    collected_tool_results: list[dict] = []
                    usage_acc: dict | None = None
                    # Always enter stream_response: L0 (list skills / model / version /
                    # whoami / status) works without a provider key. Non-L0 without a
                    # key still gets a clear notice from the agent (streamed as text).
                    aborted = False

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
                        elif token.startswith("@@thinking:"):
                            thought = token[len("@@thinking:") :]
                            if thought:
                                full_thinking += thought
                                yield await _sse_stream_text(thought, event="thinking")
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
                            # ComfyUI (etc.): image markdown with data-URI — show immediately.
                            md = token[len("@@image_markdown:"):]
                            if md:
                                full_response += ("\n\n" if full_response else "") + md
                                yield await _sse_stream_text(md, event="token")
                        elif token == "@@tool_calls":
                            pass
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
                            model=req.model or getattr(runtime, "_llm_model", None),
                            provider=getattr(runtime, "_llm_provider", None),
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

                    # Always leave a durable assistant row when we have any text
                    # (including stop/disconnect explanations). Status-only turns
                    # used to vanish with no chat bubble.
                    if aborted and not (full_response or "").strip():
                        full_response = (
                            "*(Generation stopped. History is intact — "
                            "send **continue** to resume.)*"
                        )

                    if full_response and memory:
                        tok = None
                        if isinstance(final_usage, dict):
                            tok = int(final_usage.get("total_tokens") or 0) or None
                        await memory.add_chat_message(ChatMessage(
                            session_id=session_id,
                            role=ChatMessageRole.ASSISTANT,
                            content=full_response,
                            thinking=full_thinking.strip() or None,
                            tool_calls=collected_tool_calls,
                            tool_results=collected_tool_results,
                            model=req.model or getattr(runtime, "_llm_model", None),
                            tokens=tok,
                        ))
                        # Desktop→messenger: mirror so Telegram users see desktop replies.
                        with contextlib.suppress(Exception):
                            from remedy.gateway.session_bridge import (
                                mirror_desktop_reply_to_messenger,
                            )
                            from remedy.interfaces.session_events import publish_session_event

                            ex = await memory.get_chat_session(session_id)
                            if ex is not None:
                                await mirror_desktop_reply_to_messenger(
                                    gateway, ex, full_response
                                )
                                if getattr(ex, "origin_channel", None):
                                    await publish_session_event(
                                        "message_added",
                                        session_id,
                                        origin_channel=getattr(ex, "origin_channel", None),
                                        title=getattr(ex, "title", None),
                                        role="assistant",
                                    )

                    done_payload: dict = {
                        "type": "done",
                        "request_id": request_id,
                        "status": "aborted" if aborted else "ok",
                    }
                    if isinstance(final_usage, dict) and not aborted:
                        done_payload["usage"] = final_usage
                    yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

                except asyncio.CancelledError:
                    # Client disconnect / ASGI cancel — kill shell children + CUA jobs
                    # so Stop / tab close does not leave tools running.
                    status = "cancelled"
                    # Persist whatever we already streamed. Fetch abort used to
                    # drop the assistant row so reload showed only the user turn.
                    if memory is not None:
                        note = ""
                        thinking = None
                        calls: list = []
                        results: list = []
                        with contextlib.suppress(Exception):
                            note = (full_response or "").strip()
                        with contextlib.suppress(Exception):
                            thinking = (full_thinking or "").strip() or None
                        with contextlib.suppress(Exception):
                            calls = list(collected_tool_calls)
                        with contextlib.suppress(Exception):
                            results = list(collected_tool_results)
                        if not note:
                            note = (
                                "*(Generation stopped. History is intact — "
                                "send **continue** to resume.)*"
                            )
                        with contextlib.suppress(Exception):
                            await memory.add_chat_message(
                                ChatMessage(
                                    session_id=session_id,
                                    role=ChatMessageRole.ASSISTANT,
                                    content=note,
                                    thinking=thinking,
                                    tool_calls=calls,
                                    tool_results=results,
                                    model=req.model
                                    or getattr(runtime, "_llm_model", None),
                                )
                            )
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
                        if memory is not None:
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
                                    model=req.model
                                    or getattr(runtime, "_llm_model", None),
                                )
                            )
                finally:
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
