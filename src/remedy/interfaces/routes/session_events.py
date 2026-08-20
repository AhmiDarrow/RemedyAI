"""Realtime SSE: GET /api/events/sessions."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from remedy.interfaces.api_support import sse_headers


def register_session_event_routes(app: FastAPI) -> None:
    """Attach session event SSE (independent of chat memory)."""

    @app.get("/api/events/sessions")
    async def session_events_stream():
        """SSE stream of session create/update/message events for desktop sync."""
        from remedy.interfaces.session_events import get_session_event_hub

        hub = get_session_event_hub()

        async def event_stream():
            q = await hub.subscribe()
            try:
                yield (
                    "event: hello\ndata: "
                    + json.dumps({"type": "hello", "ts": time.time()})
                    + "\n\n"
                )
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=25.0)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if ev is None:
                        break
                    payload = ev.to_dict() if hasattr(ev, "to_dict") else dict(vars(ev))
                    etype = str(payload.get("type") or "session_updated")
                    yield f"event: {etype}\ndata: {json.dumps(payload)}\n\n"
            finally:
                await hub.unsubscribe(q)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers=sse_headers(),
        )
