"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from remedy import __version__ as _remedy_version
from remedy.core.errors import SecurityError
from remedy.core.security import safe_path
from remedy.interfaces.api_models import (
    AttachmentRef,
    AttachmentUploadRequest,
    ChatRequest,
    ChatResponse,
    CommandRequest,
    CreateSessionRequest,
    MemoryAddRequest,
    MemorySearchRequest,
    SendMessageRequest,
    SettingsUpdateRequest,
    SkillInfo,
    StatusResponse,
    UpdateSessionRequest,
    WebhookPayload,
)
from remedy.interfaces.api_support import (
    _apply_llm_to_runtime,
    _BUILTIN_AGENTS,
    _BUILTIN_COMMANDS,
    _BUILTIN_MODELS,
    _default_config_path,
    _find_config_path,
    _load_config_cached,
    _serialize_toml,
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    _write_config,
    handle_slash_command,
    load_config,
    sse_headers,
)
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    catalog_models_for_provider,
    needs_first_run_setup,
    normalize_llm_settings,
    provider_credentials_ready,
)
from remedy.interfaces.config import _is_local_url
from remedy.models import (
    ChannelKind,
    ChatMessageRole,
    EventKind,
    GatewayEvent,
    MemoryEntryType,
)

logger = logging.getLogger(__name__)


def register_workspace_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- file search (project-jailed) ----------------------------------------
    def _files_base(session_id: str | None = None) -> Path:
        """Workspace root: session project_path > config project_path > env > cwd."""
        from remedy.core.workspace import (
            default_project_from_config,
            ensure_project_dir,
            resolve_project_path,
        )

        cfg = load_config()
        raw: str | None = None
        # Session override (async path sets this via query — sync helpers use config only
        # unless caller passes session path).
        if session_id and memory is not None:
            # Best-effort: memory methods are async; use config/runtime for sync helper.
            pass
        if runtime is not None and hasattr(runtime, "effective_project_path"):
            try:
                return ensure_project_dir(runtime.effective_project_path())
            except Exception:
                pass
        raw = (
            cfg.get("project_path")
            or os.environ.get("REMEDY_PROJECT_PATH")
            or os.environ.get("REMEDY_FILES_ROOT")
            or None
        )
        try:
            return ensure_project_dir(resolve_project_path(raw))
        except Exception:
            return default_project_from_config(cfg)

    def _resolve_jailed(path: str, base: Path) -> Path:
        """Resolve path under base; reject traversal."""
        from remedy.core.workspace import jail_path

        if path in (".", "", None):
            return base
        try:
            return jail_path(path, base)
        except SecurityError:
            candidate = Path(path).expanduser().resolve()
            candidate.relative_to(base)
            return candidate

    @app.get("/api/workspace")
    async def get_workspace(session_id: str | None = Query(default=None)):
        """Return the active project/workspace root for UI and tools."""
        from remedy.core.workspace import ensure_project_dir, list_workspace_entries, resolve_project_path

        root: Path | None = None
        source = "cwd"
        if session_id and memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess and sess.project_path:
                root = resolve_project_path(sess.project_path)
                source = "session"
        if root is None and runtime is not None and hasattr(runtime, "effective_project_path"):
            try:
                root = runtime.effective_project_path()
                source = "runtime"
            except Exception:
                root = None
        if root is None:
            root = _files_base()
            source = "config"
        try:
            root = ensure_project_dir(root)
        except Exception:
            pass
        return {
            "project_path": str(root),
            "source": source,
            "entries": list_workspace_entries(root),
        }

    @app.get("/api/files")
    async def list_files(
        path: str = Query(default="."),
        session_id: str | None = Query(default=None),
    ):
        """List files in a directory for @file autocompletion (jailed to project)."""
        if session_id and memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess and sess.project_path:
                from remedy.core.workspace import ensure_project_dir, resolve_project_path

                try:
                    base = ensure_project_dir(resolve_project_path(sess.project_path))
                except Exception:
                    base = _files_base()
            else:
                base = _files_base()
        else:
            base = _files_base()
        try:
            root = _resolve_jailed(path, base)
        except (SecurityError, ValueError):
            return {"files": [], "path": path, "error": "path outside allowed directory", "root": str(base)}
        try:
            if not root.exists():
                return {"files": [], "path": path, "root": str(base)}
            entries = []
            for p in sorted(root.iterdir()):
                if p.name.startswith(".") and p.name != ".":
                    continue
                try:
                    rel = str(p.relative_to(base))
                except ValueError:
                    continue
                entries.append({
                    "name": p.name,
                    "path": rel,
                    "is_dir": p.is_dir(),
                })
            return {
                "files": entries[:200],
                "path": str(root.relative_to(base) if root != base else "."),
                "root": str(base),
            }
        except Exception:
            return {"files": [], "path": path, "root": str(base)}

    @app.get("/api/files/search")
    async def search_files(
        query: str = Query(..., min_length=1),
        session_id: str | None = Query(default=None),
    ):
        """Search the project directory tree for matching files."""
        if session_id and memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess and sess.project_path:
                from remedy.core.workspace import ensure_project_dir, resolve_project_path

                try:
                    base = ensure_project_dir(resolve_project_path(sess.project_path))
                except Exception:
                    base = _files_base()
            else:
                base = _files_base()
        else:
            base = _files_base()
        # Prevent glob injection / path escapes via query
        safe_query = query.replace("/", "").replace("\\", "").replace("..", "")
        if not safe_query:
            return {"query": query, "results": [], "root": str(base)}
        try:
            results = []
            for p in base.rglob(f"*{safe_query}*"):
                if ".git" in p.parts or "__pycache__" in p.parts or "node_modules" in p.parts:
                    continue
                if p.name.startswith("."):
                    continue
                try:
                    rel = str(p.relative_to(base))
                except ValueError:
                    continue
                results.append({
                    "name": p.name,
                    "path": rel,
                    "is_dir": p.is_dir(),
                })
                if len(results) >= 50:
                    break
            return {
                "query": query,
                "results": sorted(results, key=lambda r: len(r["path"])),
                "root": str(base),
            }
        except Exception:
            return {"query": query, "results": [], "root": str(base)}

    # -- message edit / undo (user messages only) -----------------------------
    @app.post("/api/sessions/{session_id}/messages/{msg_id}/edit")
    async def edit_from_message(session_id: str, msg_id: str):
        """Begin edit-and-resend: soft-delete this user message and everything after.

        Returns the original user text so the client can load it into the composer.
        """
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        msg = await memory.get_chat_message(msg_id)
        if msg is None or msg.session_id != session_id:
            raise HTTPException(404, "Message not found")
        if msg.role != ChatMessageRole.USER:
            raise HTTPException(
                400,
                "Only user messages can be edited. Use Edit on your message to revise and resend.",
            )
        if msg.reverted:
            raise HTTPException(400, "Message already reverted")
        count = await memory.revert_from(session_id, msg_id)
        return {
            "status": "ready_to_edit",
            "msg_id": msg_id,
            "content": msg.content,
            "reverted_count": count,
        }

    @app.post("/api/sessions/{session_id}/messages/{msg_id}/revert")
    async def revert_message(session_id: str, msg_id: str):
        """Legacy alias → edit-from (user messages only, cascade to later msgs)."""
        return await edit_from_message(session_id, msg_id)

    # -- session export -------------------------------------------------------
    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        session = await memory.get_chat_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        messages = await memory.get_chat_messages(session_id, limit=10000)
        session_title = getattr(session, "title", "Session") or "Session"
        lines = [f"# {session_title}", "", f"**Session ID:** `{session_id}`", f"**Messages:** {len(messages)}", ""]
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "user")
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            created = getattr(m, "created_at", None) or (m.get("created_at") if isinstance(m, dict) else "")
            agent = getattr(m, "agent", None) or (m.get("agent") if isinstance(m, dict) else "")
            model = getattr(m, "model", None) or (m.get("model") if isinstance(m, dict) else "")
            header = f"**{role.capitalize()}**"
            if agent:
                header += f" ({agent})"
            if model:
                header += f" — {model}"
            if created:
                header += f" `{created[:19]}`"
            lines.append(header)
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")
            lines.append("---")
            lines.append("")
        markdown = "\n".join(lines)
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in session_title)[:60]
        filename = f"remedy-export-{safe_name}.md"
        return {"markdown": markdown, "filename": filename}

