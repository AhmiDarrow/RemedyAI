"""Session API routes package."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from remedy.interfaces.api_models import (
    AttachmentUploadRequest,
    BulkSessionProjectRequest,
    ChatRequest,
    CreateSessionRequest,
    SendMessageRequest,
    SessionLlmRequest,
    UpdateSessionRequest,
)
from remedy.interfaces.api_support import (
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    load_config,
    sse_headers,
)
from remedy.models import (
    ChatMessageRole,
)

logger = logging.getLogger(__name__)



def register_crud_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register crud session routes."""
    _ = gateway  # may be unused in some modules
    @app.get("/api/sessions")
    async def list_chat_sessions(
        limit: int = Query(default=100, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        if memory is None:
            return {"sessions": [], "offset": offset, "limit": limit, "has_more": False}
        from remedy.interfaces.session_public import session_to_public

        sessions = await memory.list_chat_sessions(limit=limit, offset=offset)
        has_more = len(sessions) >= limit
        return {
            "sessions": [session_to_public(s) for s in sessions],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    def _default_project_path() -> str | None:
        """Resolved default workspace from config / runtime."""
        from remedy.core.workspace import default_project_from_config

        cfg = load_config()
        if runtime is not None and hasattr(runtime, "effective_project_path"):
            try:
                return str(runtime.effective_project_path())
            except Exception:
                pass
        return str(default_project_from_config(cfg))

    @app.post("/api/sessions")
    async def create_chat_session(req: CreateSessionRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")

        from remedy.core.workspace import (
            ensure_project_dir,
            is_unset_project_path,
            resolve_project_path,
        )
        from remedy.models import ChatSession as CS

        # Omit project_path (None) → inherit global settings project if set.
        # Explicit "" / "." → no-project session (root / full access) — default for New Session.
        # New Project folder is first-run only (config init), never forced onto every session.
        if req.project_path is not None:
            raw_project = req.project_path
        else:
            raw_project = load_config().get("project_path")
        project_path = None
        if not is_unset_project_path(raw_project):
            try:
                project_path = str(ensure_project_dir(resolve_project_path(str(raw_project))))
            except Exception:
                project_path = str(resolve_project_path(str(raw_project)))

        # Stamp provider+model at create so a second project session does not
        # inherit a null bind and thrash the status bar against the first tab.
        sess_provider = (req.llm_provider or "").strip().lower() or None
        sess_model = (req.model or "").strip() or None
        if sess_model or sess_provider:
            with contextlib.suppress(Exception):
                from remedy.core.session_llm import session_llm_update_fields

                fields = session_llm_update_fields(
                    provider=sess_provider, model=sess_model
                )
                sess_provider = fields.get("llm_provider") or sess_provider
                sess_model = fields.get("model") or sess_model

        session = CS(
            title=req.title,
            model=sess_model or req.model,
            agent=req.agent,
            project_path=project_path,
            llm_provider=sess_provider,
        )
        saved = await memory.create_chat_session(session)
        return {
            "id": saved.id,
            "title": saved.title,
            "model": saved.model,
            "agent": saved.agent,
            "project_path": saved.project_path,
            "llm_provider": getattr(saved, "llm_provider", None),
            "message_count": saved.message_count,
            "origin_channel": getattr(saved, "origin_channel", None),
            "external_chat_id": getattr(saved, "external_chat_id", None),
            "external_user": getattr(saved, "external_user", None),
            "created_at": saved.created_at.isoformat() if saved.created_at else None,
            "updated_at": saved.updated_at.isoformat() if saved.updated_at else None,
        }

    @app.get("/api/sessions/{session_id}")
    async def get_chat_session(session_id: str):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        session = await memory.get_chat_session(session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        return {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "agent": session.agent,
            "project_path": session.project_path,
            "llm_provider": getattr(session, "llm_provider", None),
            "message_count": session.message_count,
            "origin_channel": getattr(session, "origin_channel", None),
            "external_chat_id": getattr(session, "external_chat_id", None),
            "external_user": getattr(session, "external_user", None),
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @app.patch("/api/sessions/{session_id}")
    async def update_chat_session(session_id: str, req: UpdateSessionRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        # exclude_unset: only fields the client sent (so project_path can be cleared)
        fields = {
            k: v
            for k, v in req.model_dump(exclude_unset=True).items()
            if v is not None or k == "project_path"
        }
        if "project_path" in fields:
            from remedy.core.workspace import (
                ensure_project_dir,
                is_unset_project_path,
                resolve_project_path,
            )

            raw = fields["project_path"]
            if is_unset_project_path(raw):
                fields["project_path"] = None
            else:
                try:
                    fields["project_path"] = str(
                        ensure_project_dir(resolve_project_path(str(raw)))
                    )
                except Exception:
                    fields["project_path"] = str(resolve_project_path(str(raw)))
        session = await memory.update_chat_session(session_id, **fields)
        if session is None:
            raise HTTPException(404, "Session not found")
        return {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "agent": session.agent,
            "project_path": session.project_path,
            "llm_provider": getattr(session, "llm_provider", None),
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @app.post("/api/sessions/bulk-project")
    async def bulk_set_session_project(req: BulkSessionProjectRequest):
        """Attach many sessions to one project folder (or clear → no project)."""
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        from remedy.core.workspace import (
            ensure_project_dir,
            is_unset_project_path,
            resolve_project_path,
        )

        ids = [str(i).strip() for i in (req.session_ids or []) if str(i).strip()]
        if not ids:
            raise HTTPException(400, "session_ids required")
        if len(ids) > 200:
            raise HTTPException(400, "At most 200 sessions per bulk move")

        project_path: str | None = None
        if not is_unset_project_path(req.project_path):
            try:
                project_path = str(
                    ensure_project_dir(resolve_project_path(str(req.project_path)))
                )
            except Exception:
                project_path = str(resolve_project_path(str(req.project_path)))

        updated: list[str] = []
        missing: list[str] = []
        for sid in ids:
            sess = await memory.update_chat_session(sid, project_path=project_path)
            if sess is None:
                missing.append(sid)
            else:
                updated.append(sid)
        return {
            "status": "ok",
            "project_path": project_path,
            "updated": updated,
            "missing": missing,
            "count": len(updated),
        }

    @app.delete("/api/sessions/{session_id}")
    async def delete_chat_session(session_id: str):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        sid = str(session_id or "").strip()
        # Stop in-flight turn + shell children before dropping the row
        with contextlib.suppress(Exception):
            from remedy.core.turn_context import abort_session as _abort_turn

            _abort_turn(sid)
        if runtime is not None:
            with contextlib.suppress(Exception):
                ss = getattr(runtime, "_streaming_sessions", None)
                if isinstance(ss, set):
                    ss.discard(sid)
        deleted = await memory.delete_chat_session(sid)
        if not deleted:
            raise HTTPException(404, "Session not found")
        # Cascade session-scoped disk artifacts (attachments / plans / undo)
        cascade: dict = {}
        with contextlib.suppress(Exception):
            from remedy.core.session_reset import purge_session_disk_artifacts

            home = load_config().get("home_dir")
            cascade = purge_session_disk_artifacts(sid, home)
        with contextlib.suppress(Exception):
            from remedy.memory.middleman import forget_session_middleman

            forget_session_middleman(sid)
        return {
            "status": "deleted",
            "session_id": sid,
            "cascade": cascade,
        }

    @app.post("/api/sessions/{session_id}/abort")
    async def abort_session(session_id: str):
        """Cooperatively stop in-flight generation for this session."""
        from remedy.core.turn_context import abort_session as _abort_turn

        n = _abort_turn(session_id)
        if runtime is not None:
            # Clear global streaming flag when this session was the active one.
            with contextlib.suppress(Exception):
                # Per-session stream set; clear this id only (not all tabs).
                ss = getattr(runtime, "_streaming_sessions", None)
                if isinstance(ss, set):
                    ss.discard(str(session_id))
        return {"status": "aborted", "session_id": session_id, "notified": n}
