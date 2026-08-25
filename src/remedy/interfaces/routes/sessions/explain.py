"""Turn explainability — GET …/turns/{turn_id}/explain."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException


def register_explain_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register explain-turn session routes."""
    _ = runtime, gateway  # unused; kept for register_sessions_routes kwargs parity

    @app.get("/api/sessions/{session_id}/turns/{turn_id}/explain")
    async def explain_session_turn(session_id: str, turn_id: str):
        """Plain-language summary of what / why / verified / remains for a turn."""
        if memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess is None:
                raise HTTPException(404, "Session not found")

        from remedy.core.explain import explain_turn
        from remedy.events.bus import default_bus

        bus = default_bus()
        rows = bus.for_turn(turn_id)
        if not rows:
            raise HTTPException(404, "Turn not found")
        return explain_turn(bus, turn_id)
