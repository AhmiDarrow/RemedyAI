"""Wire messenger channels + session bridge onto the API/desktop Gateway.

Keeps interfaces/cli.py thin: serve only calls ``attach_messengers_to_gateway``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

logger = logging.getLogger("remedy.gateway")


def attach_messengers_to_gateway(runtime: Any, gateway: Any) -> list[str]:
    """Register messenger event handler + enabled channel adapters.

    Returns list of registered messenger ids.
    """
    from remedy.gateway.channel_registry import register_messenger_channels
    from remedy.gateway.messengers import is_messenger_channel
    from remedy.gateway.session_bridge import handle_messenger_event, outbound_chunks
    from remedy.interfaces.api_support import load_config

    async def _gateway_handler(event):
        ch = event.channel.value if hasattr(event.channel, "value") else str(event.channel)
        target = (
            (event.payload or {}).get("chat_id")
            or (event.payload or {}).get("channel_id")
            or event.source_id
            or None
        )
        if is_messenger_channel(ch):
            # Refresh typing occasionally while the model streams (Telegram only).
            typing_fn = None
            adapter = gateway.get_channel(event.channel) if hasattr(gateway, "get_channel") else None
            if adapter is not None and hasattr(adapter, "send_typing") and target:
                typing_fn = adapter.send_typing

            buf: list[str] = []
            last_typing = 0.0
            sent_upto = 0
            import time as _time

            async def _flush_phone(*, final: bool) -> None:
                """Send visible text to the phone as paragraphs land.

                Waiting until the whole ReAct finishes is why Telegram
                looks dead: typing for minutes, then maybe one dump.
                """
                nonlocal sent_upto
                acc = "".join(buf)
                dest = str(target) if target else None
                if final:
                    rest = acc[sent_upto:].strip()
                    if rest:
                        for part in outbound_chunks(rest, ch):
                            await gateway.send_to(event.channel, part, target=dest)
                        sent_upto = len(acc)
                    return
                while True:
                    nxt = acc.find("\n\n", sent_upto)
                    if nxt < 0:
                        break
                    piece = acc[sent_upto:nxt].strip()
                    sent_upto = nxt + 2
                    if piece:
                        for part in outbound_chunks(piece, ch):
                            await gateway.send_to(event.channel, part, target=dest)

            async for chunk in handle_messenger_event(runtime, event):
                if chunk is not None:
                    buf.append(str(chunk))
                    yield chunk
                    now = _time.monotonic()
                    if typing_fn is not None and now - last_typing > 4.0:
                        last_typing = now
                        with contextlib.suppress(Exception):
                            await typing_fn(str(target))
                    await _flush_phone(final=False)
            await _flush_phone(final=True)
            return
        async for chunk in runtime.handle_event(event):
            if chunk is not None:
                yield chunk

    gateway.register_handler(_gateway_handler)

    registered: list[str] = []
    try:
        registered = register_messenger_channels(gateway, load_config() or {})
        if registered:
            logger.info("Messenger channels active: %s", ", ".join(registered))
    except Exception:
        logger.exception("Failed to register messenger channels")
    return registered
