"""Bridge messenger gateway events into desktop chat_sessions / chat_messages.

Ensures remote conversations appear in the desktop session list and share the
same history when the user continues in-app. Publishes realtime session events
for SSE subscribers.
"""

from __future__ import annotations

import logging
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
    if runtime is not None:
        try:
            if hasattr(runtime, "effective_project_path"):
                project_path = str(runtime.effective_project_path() or "") or None
            cfg = getattr(runtime, "config", None)
            if cfg is not None:
                model = getattr(cfg, "llm_model", None)
                agent = getattr(cfg, "name", None)
                llm_provider = getattr(cfg, "llm_provider", None)
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
    await publish_session_event(
        "message_added",
        session.id,
        origin_channel=session.origin_channel,
        message_id=str(saved.id),
        title=session.title,
        role="user",
    )
    await publish_session_event(
        "session_updated",
        session.id,
        origin_channel=session.origin_channel,
        title=session.title,
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
    await publish_session_event(
        "session_updated",
        session.id,
        origin_channel=session.origin_channel,
        title=session.title,
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
    from contextlib import suppress

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
                runtime._session_id = session.id  # type: ignore[attr-defined]
                model = getattr(getattr(runtime, "config", None), "llm_model", None)
                agent = getattr(getattr(runtime, "config", None), "name", None)
                await persist_user_message(
                    memory, session, message, model=model, agent=agent
                )
        except Exception:
            logger.exception("messenger session bridge failed")

    # Prefer full stream_response path (history + tools + continuity stack)
    reply_parts: list[str] = []
    if session is not None and hasattr(runtime, "stream_response"):
        try:
            async for chunk in runtime.stream_response(
                message, session_id=session.id
            ):
                if chunk is not None:
                    text = str(chunk)
                    # Skip internal @@ tool lifecycle markers for messenger UX
                    if text.startswith("@@"):
                        continue
                    reply_parts.append(text)
                    yield text
        except Exception:
            logger.exception("messenger stream_response failed; falling back")
            async for chunk in runtime.handle_event(event):
                if chunk is not None:
                    text = str(chunk)
                    reply_parts.append(text)
                    yield text
    else:
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


def outbound_chunks(text: str, channel: str | ChannelKind) -> list[str]:
    ch = _channel_value(channel)
    return split_message(text, ch)
