"""Outbound dial to an owner-run Grove Connect relay.

Both the PC and the phone are TCP *clients* of the relay so a phone on LTE
can meet a NATed PC. The relay only splices bytes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from remedy.connect.relay import SESSION_ID_LEN
from remedy.connect.rendezvous import parse_relay_endpoint


async def dial_relay(
    url: str,
    session_id: bytes,
    *,
    timeout: float = 15.0,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if len(session_id) != SESSION_ID_LEN:
        raise ValueError("session id must be 16 bytes")
    host, port = parse_relay_endpoint(url)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=timeout,
    )
    writer.write(session_id)
    await writer.drain()
    return reader, writer


def relay_configured(config: dict[str, Any] | None) -> str:
    raw = ""
    if isinstance(config, dict):
        raw = str(config.get("connect_relay_url") or "").strip()
    if not raw:
        return ""
    parse_relay_endpoint(raw)  # fail closed
    return raw
