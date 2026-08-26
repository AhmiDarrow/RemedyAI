"""Bridge messenger gateway events into desktop chat_sessions / chat_messages.

Ensures remote conversations appear in the desktop session list and share the
same history when the user continues in-app. Publishes realtime session events
for SSE subscribers.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from remedy.gateway.messengers import (
    external_session_id,
    heuristic_session_title,
    is_messenger_channel,
    split_message,
)
from remedy.interfaces.session_events import publish_session_event
from remedy.models import (
    ChannelKind,
    ChatMessage,
    ChatMessageRole,
    ChatSession,
    GatewayEvent,
)

logger = logging.getLogger(__name__)


def _last_desktop_muscle() -> tuple[str | None, str | None]:
    """Provider/model the owner last used on desktop — remote should match."""
    with suppress(Exception):
        from remedy.interfaces.api_support import load_config

        cfg = load_config() or {}
        last_p = str(cfg.get("last_llm_provider") or "").strip().lower() or None
        cfg_p = str(cfg.get("llm_provider") or "").strip().lower() or None
        by_p = cfg.get("last_model_by_provider") or {}
        last_m = None
        if last_p and isinstance(by_p, dict):
            last_m = str(by_p.get(last_p) or "").strip() or None
        # Do not pair xAI with the launch DeepSeek id (or the reverse).
        if not last_m and last_p and (not cfg_p or last_p == cfg_p):
            last_m = str(cfg.get("llm_model") or "").strip() or None
        if not last_p:
            last_p = cfg_p
        return last_p, last_m
    return None, None


async def _attach_to_focused_session(
    memory: Any,
    *,
    channel: str,
    external_chat_id: str,
    username: str | None,
) -> Any:
    """Use the focused desktop chat when the owner lives in one endless session."""
    focused = ""
    with suppress(Exception):
        from remedy.core.computer.host_bridge import get_host_bridge

        focused = str(get_host_bridge().focused_session_id() or "").strip()
    if not focused or focused.startswith("msg:"):
        return None
    sess = await memory.get_chat_session(focused)
    if sess is None:
        return None
    oc = str(getattr(sess, "origin_channel", None) or "").strip().lower()
    ext = str(getattr(sess, "external_chat_id", None) or "").strip()
    if oc and oc != channel:
        return None
    if ext and ext != str(external_chat_id or "").strip():
        return None
    updates: dict[str, Any] = {}
    if not oc:
        updates["origin_channel"] = channel
    if not ext:
        updates["external_chat_id"] = str(external_chat_id or "").strip()
    if username and not getattr(sess, "external_user", None):
        updates["external_user"] = str(username)[:120]
    if updates:
        try:
            updated = await memory.update_chat_session(focused, **updates)
            if updated is not None:
                return updated
        except Exception:
            logger.debug("attach messenger origin to focused session failed", exc_info=True)
    return sess


def _channel_value(channel: Any) -> str:
    if hasattr(channel, "value"):
        return str(channel.value)
    return str(channel or "").strip().lower()


async def resolve_or_create_messenger_session(
    memory: Any,
    *,
    channel: str,
    external_chat_id: str,
    username: str | None = None,
    chat_title: str | None = None,
    first_message: str | None = None,
    project_path: str | None = None,
    model: str | None = None,
    agent: str | None = None,
    llm_provider: str | None = None,
) -> ChatSession:
    """Return existing messenger session or create one with stable id."""
    ch = str(channel or "").strip().lower()
    ext = str(external_chat_id or "").strip() or "default"
    sid = external_session_id(ch, ext)

    # Endless desktop session: Telegram/Discord join the focused tab instead
    # of spawning a parallel ``msg:telegram:…`` thread that steals continuity.
    attached = await _attach_to_focused_session(
        memory,
        channel=ch,
        external_chat_id=ext,
        username=username,
    )
    if attached is not None:
        return attached

    existing = await memory.get_chat_session(sid)
    if existing is not None:
        # Refresh handle if we learn a username later
        if username and not existing.external_user:
            try:
                updated = await memory.update_chat_session(
                    sid, external_user=str(username)[:120]
                )
                if updated:
                    return updated
            except Exception:
                pass
        return existing

    # Also try lookup by origin columns (legacy / alternate ids)
    try:
        by_ext = await memory.find_session_by_external(ch, ext)
        if by_ext is not None:
            return by_ext
    except Exception:
        pass

    title = heuristic_session_title(
        ch,
        username=username,
        chat_title=chat_title,
        first_message=first_message,
    )
    session = ChatSession(
        id=sid,
        title=title,
        model=model,
        agent=agent,
        project_path=project_path,
        llm_provider=llm_provider,
        origin_channel=ch,
        external_chat_id=ext,
        external_user=(str(username)[:120] if username else None),
    )
    saved = await memory.create_chat_session(session)
    await publish_session_event(
        "session_created",
        saved.id,
        origin_channel=ch,
        title=saved.title,
        message_count=saved.message_count,
    )
    return saved


async def ensure_session_for_event(
    memory: Any,
    event: GatewayEvent,
    *,
    runtime: Any = None,
) -> ChatSession | None:
    """Resolve chat session for a gateway event; None for non-messenger channels."""
    channel = _channel_value(event.channel)
    if channel in ("cli", "web", "api") and not is_messenger_channel(channel):
        # CLI may still have session_id from desktop; not auto-created here
        if event.session_id:
            try:
                return await memory.get_chat_session(str(event.session_id))
            except Exception:
                return None
        return None

    if not is_messenger_channel(channel):
        return None

    payload = event.payload or {}
    external = (
        str(payload.get("chat_id") or payload.get("channel_id") or payload.get("room_id") or "")
        or str(event.session_id or "")
        or str(event.source_id or "")
        or "default"
    )
    username = payload.get("username") or payload.get("user_name")
    chat_title = payload.get("chat_title") or payload.get("channel_name")
    message = str(payload.get("message") or "")

    project_path = None
    model = None
    agent = None
    llm_provider = None
    last_p, last_m = _last_desktop_muscle()
    if last_p:
        llm_provider = last_p
    if last_m:
        model = last_m
    if runtime is not None:
        try:
            if hasattr(runtime, "effective_project_path"):
                project_path = str(runtime.effective_project_path() or "") or None
            cfg = getattr(runtime, "config", None)
            if cfg is not None:
                agent = getattr(cfg, "name", None)
                if not llm_provider:
                    llm_provider = getattr(cfg, "llm_provider", None)
                if not model:
                    model = getattr(cfg, "llm_model", None)
        except Exception:
            pass

    return await resolve_or_create_messenger_session(
        memory,
        channel=channel,
        external_chat_id=external,
        username=str(username) if username else None,
        chat_title=str(chat_title) if chat_title else None,
        first_message=message or None,
        project_path=project_path,
        model=model,
        agent=agent,
        llm_provider=llm_provider,
    )


async def persist_user_message(
    memory: Any,
    session: ChatSession,
    content: str,
    *,
    model: str | None = None,
    agent: str | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        session_id=session.id,
        role=ChatMessageRole.USER,
        content=content or "",
        model=model,
        agent=agent,
    )
    saved = await memory.add_chat_message(msg)
    # One event is enough for desktop SSE (list + active thread refresh).
    await publish_session_event(
        "message_added",
        session.id,
        origin_channel=session.origin_channel,
        message_id=str(saved.id),
        title=session.title,
        role="user",
        message_count=getattr(session, "message_count", None),
    )
    return saved


async def persist_assistant_message(
    memory: Any,
    session: ChatSession,
    content: str,
    *,
    model: str | None = None,
    agent: str | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        session_id=session.id,
        role=ChatMessageRole.ASSISTANT,
        content=content or "",
        model=model,
        agent=agent,
    )
    saved = await memory.add_chat_message(msg)
    await publish_session_event(
        "message_added",
        session.id,
        origin_channel=session.origin_channel,
        message_id=str(saved.id),
        title=session.title,
        role="assistant",
    )
    return saved


async def handle_messenger_event(
    runtime: Any,
    event: GatewayEvent,
) -> Any:
    """Process a messenger GatewayEvent: session + history + agent reply chunks.

    Yields response text chunks (same as handle_event). Persists chat messages
    when runtime.memory is available.
    """

    memory = getattr(runtime, "memory", None)
    channel = _channel_value(event.channel)
    message = str((event.payload or {}).get("message") or "").strip()
    if not message:
        return

    session: ChatSession | None = None
    if memory is not None and is_messenger_channel(channel):
        try:
            session = await ensure_session_for_event(memory, event, runtime=runtime)
            if session is not None:
                event.session_id = session.id
                # Do not write runtime._session_id here — a desktop tab mid-ReAct
                # shares this process. begin_turn / ContextVar owns isolation;
                # the fallback write below only runs when no turn is active.
        except Exception:
            logger.exception("messenger session bridge failed")

    # Prefer full stream_response path (history + tools + continuity stack)
    reply_parts: list[str] = []
    used_stream_response = False
    if session is not None and hasattr(runtime, "stream_response"):
        claimed = False
        claim_epoch = 0
        try:
            from remedy.core.turn_context import (
                release_session_stream_claim,
                stream_claim_epoch,
                try_claim_session_stream,
            )

            if not try_claim_session_stream(session.id):
                # Endless session: Telegram joins the live turn instead of
                # opening a second thread that fights continuity.
                steered = False
                with suppress(Exception):
                    from remedy.core.turn_context import push_nudge

                    steered = bool(push_nudge(session.id, message))
                if steered and memory is not None:
                    with suppress(Exception):
                        model = getattr(getattr(runtime, "config", None), "llm_model", None)
                        agent = getattr(getattr(runtime, "config", None), "name", None)
                        await persist_user_message(
                            memory, session, message, model=model, agent=agent
                        )
                busy = (
                    "*(Got it — folding that into the turn that's already going.)*"
                    if steered
                    else (
                        "*(This chat is already generating a reply — "
                        "try again in a moment.)*"
                    )
                )
                reply_parts.append(busy)
                yield busy
                return
            claimed = True
            claim_epoch = stream_claim_epoch(session.id)
            last_p, last_m = _last_desktop_muscle()
            if memory is not None and last_p:
                cur_p = str(getattr(session, "llm_provider", "") or "").strip().lower()
                cur_m = str(getattr(session, "model", "") or "").strip()
                if cur_p != last_p or (last_m and cur_m != last_m):
                    with suppress(Exception):
                        updated = await memory.update_chat_session(
                            session.id,
                            llm_provider=last_p,
                            model=last_m or getattr(session, "model", None),
                        )
                        if updated is not None:
                            session = updated
            if memory is not None:
                model = getattr(session, "model", None) or getattr(
                    getattr(runtime, "config", None), "llm_model", None
                )
                agent = getattr(getattr(runtime, "config", None), "name", None)
                await persist_user_message(
                    memory, session, message, model=model, agent=agent
                )
            used_stream_response = True
            async for chunk in runtime.stream_response(
                message,
                session_id=session.id,
                model=getattr(session, "model", None),
                provider=getattr(session, "llm_provider", None),
            ):
                if chunk is not None:
                    text = str(chunk)
                    # Skip internal @@ tool lifecycle markers for messenger UX
                    if text.startswith("@@"):
                        continue
                    reply_parts.append(text)
                    yield text
        except asyncio.CancelledError:
            # CPython 3.12 sticky cancel — uncancel so persist/abort awaits run.
            _task = asyncio.current_task()
            if _task is not None:
                with suppress(Exception):
                    _task.uncancel()
            # Mirror desktop SSE persist-on-cancel — user row already saved.
            note = "".join(reply_parts).strip()
            if not note:
                note = (
                    "*(Generation stopped. History is intact — "
                    "send **continue** to resume.)*"
                )
            if memory is not None:
                with suppress(Exception):
                    model = getattr(getattr(runtime, "config", None), "llm_model", None)
                    agent = getattr(getattr(runtime, "config", None), "name", None)
                    await persist_assistant_message(
                        memory, session, note, model=model, agent=agent
                    )
                    reply_parts = [note]
            with suppress(Exception):
                from remedy.core.turn_context import abort_session as _abort_turn

                _abort_turn(session.id, epoch=claim_epoch)
            raise
        except Exception:
            logger.exception("messenger stream_response failed; falling back")
            used_stream_response = False
            async for chunk in runtime.handle_event(event):
                if chunk is not None:
                    text = str(chunk)
                    reply_parts.append(text)
                    yield text
        finally:
            if claimed:
                with suppress(Exception):
                    from remedy.core.turn_context import release_session_stream_claim

                    release_session_stream_claim(session.id, epoch=claim_epoch)
    else:
        if memory is not None and session is not None:
            with suppress(Exception):
                model = getattr(getattr(runtime, "config", None), "llm_model", None)
                agent = getattr(getattr(runtime, "config", None), "name", None)
                await persist_user_message(
                    memory, session, message, model=model, agent=agent
                )
        async for chunk in runtime.handle_event(event):
            if chunk is not None:
                text = str(chunk)
                reply_parts.append(text)
                yield text

    full = "".join(reply_parts).strip()
    if memory is not None and session is not None and full:
        with suppress(Exception):
            model = getattr(getattr(runtime, "config", None), "llm_model", None)
            agent = getattr(getattr(runtime, "config", None), "name", None)
            await persist_assistant_message(
                memory, session, full, model=model, agent=agent
            )
    # stream_response already runs schedule_post_turn_prep in finally.
    # Fallback handle_event does not — keep one call on that path only.
    if session is not None:
        with suppress(Exception):
            from remedy.core.turn_context import in_active_turn

            runtime._origin_channel = channel
            if not in_active_turn():
                runtime._session_id = session.id
        if full:
            with suppress(Exception):
                runtime._last_assistant_text = full[-12000:]
        if not used_stream_response:
            with suppress(Exception):
                from remedy.core.agent_post_turn import schedule_post_turn_prep

                schedule_post_turn_prep(
                    runtime,
                    message=message or "",
                    session_id=str(session.id),
                )


def outbound_chunks(text: str, channel: str | ChannelKind) -> list[str]:
    ch = _channel_value(channel)
    return split_message(text, ch)


async def mirror_desktop_reply_to_messenger(
    gateway: Any,
    session: Any,
    text: str,
) -> bool:
    """Push a desktop assistant reply to the session's origin messenger chat.

    Telegram→desktop is handled by the gateway poll path. Desktop→Telegram was
    missing: users chatting in a ``msg:telegram:…`` session only saw replies
    locally. Best-effort; never raises into the chat stream.
    """
    if gateway is None or not (text or "").strip():
        return False
    ch = _channel_value(getattr(session, "origin_channel", None))
    target = str(getattr(session, "external_chat_id", None) or "").strip()
    if not ch or not target or not is_messenger_channel(ch):
        return False
    try:
        kind = ChannelKind(ch)
    except Exception:
        logger.debug("mirror: unknown channel %s", ch)
        return False
    ok_any = False
    try:
        for part in outbound_chunks(text, ch):
            sent = await gateway.send_to(kind, part, target=target)
            ok_any = ok_any or bool(sent)
        if ok_any:
            logger.info(
                "Mirrored desktop reply to %s chat_id=%s (%d chars)",
                ch,
                target,
                len(text),
            )
        else:
            logger.warning(
                "Mirror to %s chat_id=%s failed (channel down or send error)",
                ch,
                target,
            )
    except Exception:
        logger.exception("mirror_desktop_reply_to_messenger failed ch=%s", ch)
        return False
    return ok_any
