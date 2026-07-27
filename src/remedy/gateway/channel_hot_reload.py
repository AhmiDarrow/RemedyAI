"""Restart messenger channels after Settings save (no full app quit)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def reload_messenger_channels(gateway: Any, cfg: dict | None = None) -> list[str]:
    """Stop messenger adapters, re-register from config, start them again."""
    if gateway is None:
        return []
    from remedy.gateway.channel_registry import register_messenger_channels
    from remedy.gateway.messengers import INTERNAL_CHANNELS, is_messenger_channel
    from remedy.interfaces.api_support import load_config

    cfg = cfg if isinstance(cfg, dict) else (load_config() or {})

    # Stop + drop only messenger channels
    for kind in list(getattr(gateway, "channels", []) or []):
        val = kind.value if hasattr(kind, "value") else str(kind)
        if val in INTERNAL_CHANNELS or not is_messenger_channel(val):
            continue
        ch = gateway.get_channel(kind) if hasattr(gateway, "get_channel") else None
        if ch is not None:
            try:
                await ch.stop()
            except Exception:
                logger.debug("stop %s failed", val, exc_info=True)
        try:
            gateway._channels.pop(kind, None)  # type: ignore[attr-defined]
        except Exception:
            pass

    registered = register_messenger_channels(gateway, cfg)
    if getattr(gateway, "running", False):
        for kind in list(getattr(gateway, "channels", []) or []):
            val = kind.value if hasattr(kind, "value") else str(kind)
            if not is_messenger_channel(val):
                continue
            ch = gateway.get_channel(kind)
            if ch is not None and not ch.running:
                try:
                    await ch.start()
                except Exception:
                    logger.exception("start %s after reload failed", val)
    logger.info("Messenger hot-reload: %s", ", ".join(registered) or "(none)")
    return registered
