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



def register_attachments_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register attachments session routes."""
    _ = gateway  # may be unused in some modules
    @app.post("/api/sessions/{session_id}/attachments")
    async def upload_attachment_json(session_id: str, req: AttachmentUploadRequest):
        """Store a dropped/pasted file (JSON + base64) and return a path ref.

        Prefer this over multipart so frozen desktop sidecars do not need
        python-multipart.
        """
        import base64

        from remedy.interfaces.attachments import MAX_ATTACHMENT_BYTES, save_upload

        try:
            raw = base64.b64decode(req.data_base64, validate=False)
        except Exception as e:
            raise HTTPException(400, f"Invalid base64 payload: {e}") from e
        if not raw:
            raise HTTPException(400, "Empty file")
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                413,
                f"File too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)",
            )
        home = None
        with contextlib.suppress(Exception):
            home = load_config().get("home_dir")
        try:
            meta = save_upload(
                session_id=session_id,
                filename=req.filename or "upload.bin",
                data=raw,
                content_type=req.content_type,
                home_dir=home,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return meta

    @app.get("/api/sessions/{session_id}/attachments/{filename}")
    async def get_attachment(session_id: str, filename: str):
        from remedy.interfaces.attachments import session_attachments_dir

        home = None
        with contextlib.suppress(Exception):
            home = load_config().get("home_dir")
        directory = session_attachments_dir(session_id, home)
        # Prevent path traversal (basename + relative_to — not startswith prefix)
        safe = Path(filename).name
        if not safe or safe in (".", ".."):
            raise HTTPException(400, "Invalid path")
        try:
            root = directory.resolve()
            path = (directory / safe).resolve()
            path.relative_to(root)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, "Invalid path") from exc
        if not path.is_file():
            raise HTTPException(404, "Attachment not found")
        return FileResponse(path, filename=safe)
