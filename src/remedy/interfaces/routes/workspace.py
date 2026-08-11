"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request

from remedy.core.errors import SecurityError
from remedy.interfaces.api_models import (
    ImportSessionRequest,
)
from remedy.interfaces.api_support import (
    load_config,
)
from remedy.models import (
    ChatMessageRole,
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
        # Explicit jail-root override wins over runtime/config: tests and
        # headless callers pin the files browser to a temp root, and the
        # escape check depends on it — if runtime's project path (e.g. drive
        # root when unset) took precedence, ".." would silently stay inside.
        env_root = os.environ.get("REMEDY_FILES_ROOT") or os.environ.get(
            "REMEDY_PROJECT_PATH"
        )
        if env_root:
            try:
                return ensure_project_dir(resolve_project_path(env_root))
            except Exception:
                pass
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
        raw = cfg.get("project_path") or None
        try:
            return ensure_project_dir(resolve_project_path(raw))
        except Exception:
            return default_project_from_config(cfg)

    def _resolve_jailed(path: str, base: Path) -> Path:
        """Resolve path under base; reject traversal.

        Do not re-resolve relative paths against process CWD after jail_path
        fails — ``Path("..").resolve()`` can land *on* the base when the app
        cwd is a child of the project root, which silently accepts escapes.
        Absolute paths under base are already handled by ``jail_path``.
        """
        from remedy.core.workspace import jail_path

        if path in (".", "", None):
            return base
        return jail_path(path, base)

    @app.get("/api/media")
    async def serve_local_media(
        path: str = Query(..., description="Absolute or project-relative image path"),
    ):
        """Serve a local image for chat markdown (provider-agnostic).

        Models embed ``![alt](assets/foo.png)`` or absolute Windows paths.
        WebView cannot load bare filesystem paths — the desktop rewrites those
        to this endpoint (auth via Bearer / loopback token).
        """
        from fastapi.responses import FileResponse

        raw = (path or "").strip().strip('"').strip("'").strip("<>")
        if raw.lower().startswith("file:"):
            raw = raw[5:].lstrip("/\\")
            # file:///C:/Users/... → C:/Users/...
            if len(raw) >= 2 and raw[1] == ":":
                pass
            elif raw.startswith("/") and len(raw) >= 3 and raw[2] == ":":
                raw = raw[1:]
        if not raw:
            raise HTTPException(400, "path required")

        candidate: Path | None = None
        try:
            p = Path(raw).expanduser()
            if p.is_absolute():
                candidate = p.resolve()
            else:
                # Relative: try project base first, then ~/.remedy (session attachments).
                base = _files_base()
                candidate = (base / raw).resolve()
                if not candidate.is_file():
                    with contextlib.suppress(Exception):
                        home = Path(
                            load_config().get("home_dir")
                            or (Path.home() / ".remedy")
                        ).expanduser()
                        home_cand = (home / raw).resolve()
                        if home_cand.is_file():
                            candidate = home_cand
                # Bare filename (e.g. remedy_comfy_00019_.png) — search attachments tree
                if (
                    candidate is None or not candidate.is_file()
                ) and "/" not in raw.replace("\\", "/") and raw.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg")
                ):
                    with contextlib.suppress(Exception):
                        home = Path(
                            load_config().get("home_dir")
                            or (Path.home() / ".remedy")
                        ).expanduser()
                        att = home / "attachments"
                        if att.is_dir():
                            hits = list(att.glob(f"**/{Path(raw).name}"))
                            # Prefer most recently modified
                            hits.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                            if hits:
                                candidate = hits[0].resolve()
        except Exception as exc:
            raise HTTPException(400, f"invalid path: {exc}") from exc

        if candidate is None or not candidate.is_file():
            raise HTTPException(404, "media not found")

        # Never serve under ~/.remedy/auth (or $REMEDY_HOME/auth) — even if
        # the broad ~/.remedy root allowlist would otherwise accept it.
        try:
            from remedy.core.security import refuse_protected_secret_path

            refuse_protected_secret_path(candidate)
        except SecurityError as exc:
            raise HTTPException(
                403, "path is a protected Remedy secrets location"
            ) from exc

        # Jail: project roots, runtime allowed roots, and ~/.remedy
        roots: list[Path] = []
        with contextlib.suppress(Exception):
            roots.append(_files_base().resolve())
        if runtime is not None and hasattr(runtime, "allowed_roots"):
            with contextlib.suppress(Exception):
                for r in runtime.allowed_roots() or []:
                    roots.append(Path(r).resolve())
        with contextlib.suppress(Exception):
            home = Path(load_config().get("home_dir") or (Path.home() / ".remedy"))
            roots.append(home.expanduser().resolve())
        # Always allow default ~/.remedy (session attachments) even if home_dir differs
        with contextlib.suppress(Exception):
            roots.append((Path.home() / ".remedy").resolve())
        # Always allow reading under the default project if configured
        with contextlib.suppress(Exception):
            pp = load_config().get("project_path")
            if pp:
                roots.append(Path(str(pp)).expanduser().resolve())

        allowed = False
        for root in roots:
            try:
                candidate.relative_to(root)
                allowed = True
                break
            except ValueError:
                continue
        if not allowed:
            raise HTTPException(403, "path outside allowed roots")

        suffix = candidate.suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".bmp": "image/bmp",
            ".ico": "image/x-icon",
        }
        # Cap huge files (chat previews)
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            raise HTTPException(404, f"cannot read: {exc}") from exc
        if size > 25 * 1024 * 1024:
            raise HTTPException(413, "media too large (25 MB max)")

        # Known types stream as-is; unknown image-like files normalize to PNG via Pillow.
        if suffix in media_types:
            return FileResponse(
                candidate,
                media_type=media_types[suffix],
                filename=candidate.name,
                headers={
                    "Cache-Control": "private, max-age=120",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        try:
            from io import BytesIO

            from fastapi.responses import Response
            from PIL import Image

            with Image.open(candidate) as im:
                im = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im
                buf = BytesIO()
                im.save(buf, format="PNG", optimize=True)
            return Response(
                content=buf.getvalue(),
                media_type="image/png",
                headers={
                    "Cache-Control": "private, max-age=120",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": f'inline; filename="{candidate.stem}.png"',
                },
            )
        except Exception as exc:
            raise HTTPException(
                415, f"unsupported media type: {suffix or 'none'} ({exc})"
            ) from exc

    @app.get("/api/workspace")
    async def get_workspace(session_id: str | None = Query(default=None)):
        """Return the active project/workspace root for UI and tools."""
        from remedy.core.workspace import (
            ensure_project_dir,
            list_workspace_entries,
            resolve_project_path,
        )

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
        with contextlib.suppress(Exception):
            root = ensure_project_dir(root)
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
        query: str | None = Query(default=None, min_length=1),
        q: str | None = Query(default=None, min_length=1, description="Alias for query"),
        session_id: str | None = Query(default=None),
        path: str | None = Query(
            default=None,
            description="Optional root (absolute or project-relative); default project root",
        ),
    ):
        """Search the project directory tree for matching files."""
        query = (query or q or "").strip()
        if not query:
            raise HTTPException(400, "query (or q) is required")
        if path and str(path).strip():
            try:
                from remedy.core.workspace import ensure_project_dir, resolve_project_path

                base = ensure_project_dir(resolve_project_path(str(path).strip()))
            except Exception:
                base = _files_base()
        elif session_id and memory is not None:
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
        # Bound walk — home-scoped trees can hang with unbounded rglob
        skip_dirs = {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            "AppData",
            "Library",
            "Caches",
            ".cache",
            "target",
        }
        max_results = 50
        max_scanned = 8000
        max_depth = 8
        try:
            results: list[dict] = []
            scanned = 0
            base_resolved = base.resolve()
            for root, dirs, files in os.walk(base_resolved):
                # prune depth + noise dirs
                try:
                    depth = len(Path(root).relative_to(base_resolved).parts)
                except ValueError:
                    depth = 0
                if depth >= max_depth:
                    dirs[:] = []
                else:
                    dirs[:] = [
                        d
                        for d in dirs
                        if d not in skip_dirs and not d.startswith(".")
                    ]
                for name in files + list(dirs):
                    scanned += 1
                    if scanned > max_scanned:
                        break
                    if safe_query.lower() not in name.lower():
                        continue
                    if name.startswith("."):
                        continue
                    p = Path(root) / name
                    try:
                        rel = str(p.relative_to(base_resolved))
                    except ValueError:
                        continue
                    results.append(
                        {
                            "name": name,
                            "path": rel,
                            "is_dir": p.is_dir(),
                        }
                    )
                    if len(results) >= max_results:
                        break
                if len(results) >= max_results or scanned > max_scanned:
                    break
            return {
                "query": query,
                "results": sorted(results, key=lambda r: len(r["path"])),
                "root": str(base),
                "scanned": scanned,
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
        # Capture full original prompt before soft-delete (client loads this into composer).
        original = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        count = await memory.revert_from(session_id, msg_id)
        return {
            "status": "ready_to_edit",
            "msg_id": msg_id,
            "content": original,
            "text": original,  # alias for older clients
            "reverted_count": count,
        }

    @app.post("/api/sessions/{session_id}/messages/{msg_id}/revert")
    async def revert_message(session_id: str, msg_id: str):
        """Legacy alias → edit-from (user messages only, cascade to later msgs)."""
        return await edit_from_message(session_id, msg_id)

    # -- interactive time travel ---------------------------------------------
    @app.get("/api/sessions/{session_id}/timeline")
    async def session_timeline(session_id: str):
        """Visual timeline of user/assistant steps for the Time Travel browser."""
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        from remedy.core.time_travel import build_timeline

        msgs = await memory.get_chat_messages(session_id, limit=500)
        steps = build_timeline(msgs)
        return {
            "session_id": session_id,
            "steps": steps,
            "count": len(steps),
        }

    @app.post("/api/sessions/{session_id}/time-travel")
    async def session_time_travel(session_id: str, request: Request):
        """Roll chat + best-effort workspace files back to a timeline step.

        Body: ``{ "message_id": "<id>" }`` — typically a user-step id. Restores
        file_write undo log entries at/after that point and soft-deletes later
        chat messages.
        """
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        msg_id = str((payload or {}).get("message_id") or "").strip()
        if not msg_id:
            raise HTTPException(400, "message_id required")

        msg = await memory.get_chat_message(msg_id)
        if msg is None or msg.session_id != session_id:
            raise HTTPException(404, "Message not found")
        if msg.reverted:
            raise HTTPException(400, "Message already reverted")

        # Assistant nodes restore from their parent user turn when possible
        # so "step 3 bubble" rolls back to the start of that step.
        target_id = msg_id
        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
        if role == "assistant":
            # Find nearest prior user message
            all_msgs = await memory.get_chat_messages(session_id, limit=500)
            prev_user = None
            for m in all_msgs:
                if m.id == msg_id:
                    break
                r = m.role.value if hasattr(m.role, "value") else str(m.role)
                if r == "user" and not m.reverted:
                    prev_user = m
            if prev_user is not None:
                target_id = prev_user.id
                msg = prev_user

        cut_at = msg.created_at.isoformat() if hasattr(msg.created_at, "isoformat") else str(msg.created_at or "")
        original = msg.content if isinstance(msg.content, str) else str(msg.content or "")

        # Workspace file restore (best-effort) before soft-delete markers.
        # Prefer message_id cut (exact undo tags) with timestamp fallback.
        files = {
            "restored": 0,
            "deleted": 0,
            "skipped": 0,
            "blocked": 0,
            "paths": [],
        }
        try:
            from remedy.core.time_travel import SessionUndoLog

            home = None
            if runtime is not None:
                home = getattr(getattr(runtime, "config", None), "home_dir", None)
            if not home:
                cfg = load_config()
                home = cfg.get("home_dir")
            files = SessionUndoLog(home).restore_after(
                session_id,
                cut_message_id=str(target_id),
                cut_created_at=cut_at or None,
            )
        except Exception as exc:
            logger.debug("time-travel file restore: %s", exc)

        # Drop mid-task checkpoints after the cut (session-scoped)
        dropped_cp = 0
        try:
            from remedy.core.checkpoint import CheckpointStore

            home = None
            if runtime is not None:
                home = getattr(getattr(runtime, "config", None), "home_dir", None)
            store = CheckpointStore(home)
            for cp in store.list_for_session(session_id, limit=100):
                if cut_at and str(cp.created_at) >= cut_at:
                    path = store._path(cp.id)  # noqa: SLF001
                    if path.is_file():
                        path.unlink(missing_ok=True)
                        dropped_cp += 1
        except Exception:
            pass

        # Clear session brief on runtime if present (context memory)
        with contextlib.suppress(Exception):
            if runtime is not None and hasattr(runtime, "_session_brief"):
                runtime._session_brief = None

        count = await memory.revert_from(session_id, target_id)
        return {
            "status": "restored",
            "message_id": target_id,
            "content": original,
            "reverted_count": count,
            "files": files,
            "checkpoints_dropped": dropped_cp,
        }

    # -- session export / import (plain text) ---------------------------------
    @app.get("/api/sessions/{session_id}/export")
    async def export_session(
        session_id: str,
        format: str = Query(default="txt", description="txt (default) or md"),
    ):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        session = await memory.get_chat_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        # Cap rows hard — tool dumps dominate export size/time.
        messages = await memory.get_chat_messages(session_id, limit=2000)
        from remedy.memory.session_io import (
            format_session_markdown,
            format_session_txt,
            safe_filename_stem,
        )

        session_title = getattr(session, "title", "Session") or "Session"
        fmt = (format or "txt").strip().lower()
        if fmt in ("md", "markdown"):
            body = format_session_markdown(
                title=session_title, session_id=session_id, messages=messages
            )
            ext = "md"
        else:
            body = format_session_txt(
                title=session_title,
                session_id=session_id,
                messages=messages,
                model=getattr(session, "model", None),
                agent=getattr(session, "agent", None),
            )
            ext = "txt"
        safe_name = safe_filename_stem(session_title)
        filename = f"remedy-export-{safe_name}.{ext}"
        # ``text`` is canonical; ``markdown`` kept for older desktop clients.
        return {
            "text": body,
            "markdown": body,
            "filename": filename,
            "format": ext,
        }

    @app.post("/api/sessions/import")
    async def import_session(req: ImportSessionRequest):
        """Create a new chat session from plain-text or legacy markdown export."""
        from remedy.memory.session_io import parse_session_text
        from remedy.models import ChatMessage, ChatMessageRole
        from remedy.models import ChatSession as CS

        if memory is None:
            raise HTTPException(503, "Memory store not available")

        # Cap import size to avoid RAM/DB floods from huge dumps.
        _IMPORT_MAX_CHARS = 2_000_000
        _IMPORT_MAX_MESSAGES = 2_000

        text = (req.text or "").strip()
        if not text and req.path:
            from remedy.core.security import is_protected_secret_path
            from remedy.core.workspace import (
                allowed_roots_for_scope,
                default_project_from_config,
                resolve_under_roots,
            )

            cfg = load_config()
            scope = str(cfg.get("access_scope") or "home")
            project = default_project_from_config(cfg)
            roots = allowed_roots_for_scope(scope, project)
            try:
                path = resolve_under_roots(
                    str(req.path), roots, access_scope=scope
                )
            except Exception as exc:
                raise HTTPException(400, f"Path not allowed: {exc}") from exc
            if is_protected_secret_path(path):
                raise HTTPException(
                    400,
                    "Path not allowed: protected Remedy secrets location",
                )
            if not path.is_file():
                raise HTTPException(404, f"File not found: {path}")
            try:
                # Bound read — refuse multi-GB "imports" of arbitrary files.
                raw_bytes = path.read_bytes()
            except OSError as exc:
                raise HTTPException(400, f"Cannot read path: {exc}") from exc
            if len(raw_bytes) > _IMPORT_MAX_CHARS * 4:
                raise HTTPException(
                    400,
                    f"Import file too large (max ~{_IMPORT_MAX_CHARS} chars)",
                )
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_bytes.decode("utf-8", errors="replace")

        if not text.strip():
            raise HTTPException(400, "Provide text or path to a session .txt / .md export")
        if len(text) > _IMPORT_MAX_CHARS:
            raise HTTPException(
                400,
                f"Import text too large ({len(text)} > {_IMPORT_MAX_CHARS} chars)",
            )

        try:
            parsed = parse_session_text(text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if len(parsed.messages) > _IMPORT_MAX_MESSAGES:
            raise HTTPException(
                400,
                f"Import has too many messages ({len(parsed.messages)} > {_IMPORT_MAX_MESSAGES})",
            )

        title = (req.title or parsed.title or "Imported Session").strip()
        model = req.model or parsed.model
        agent = req.agent or parsed.agent

        from remedy.core.workspace import ensure_project_dir, resolve_project_path

        raw_project = req.project_path or load_config().get("project_path")
        project_path = None
        if raw_project and str(raw_project).strip() and str(raw_project).strip() not in (".", "./"):
            try:
                project_path = str(ensure_project_dir(resolve_project_path(str(raw_project))))
            except Exception:
                project_path = str(resolve_project_path(str(raw_project)))

        session = CS(
            title=title[:200],
            model=model,
            agent=agent,
            project_path=project_path,
        )
        saved = await memory.create_chat_session(session)

        role_map = {
            "user": ChatMessageRole.USER,
            "assistant": ChatMessageRole.ASSISTANT,
            "system": ChatMessageRole.SYSTEM,
            "tool": ChatMessageRole.TOOL,
        }
        imported = 0
        for pm in parsed.messages:
            role = role_map.get((pm.role or "user").lower(), ChatMessageRole.USER)
            if role == ChatMessageRole.SYSTEM and not (pm.content or "").strip():
                continue
            msg = ChatMessage(
                session_id=saved.id,
                role=role,
                content=pm.content or "",
                model=pm.model or model,
                agent=pm.agent or agent,
            )
            await memory.add_chat_message(msg)
            imported += 1

        refreshed = await memory.get_chat_session(saved.id)
        return {
            "id": saved.id,
            "title": refreshed.title if refreshed else title,
            "model": refreshed.model if refreshed else model,
            "agent": refreshed.agent if refreshed else agent,
            "project_path": refreshed.project_path if refreshed else project_path,
            "message_count": refreshed.message_count if refreshed else imported,
            "imported_messages": imported,
            "created_at": saved.created_at.isoformat() if saved.created_at else None,
            "updated_at": (
                refreshed.updated_at.isoformat()
                if refreshed and refreshed.updated_at
                else None
            ),
        }

