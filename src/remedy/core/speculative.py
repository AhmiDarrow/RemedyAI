"""Speculative continuity prep — background brief/memory work off the hot path.

While the frontier model streams or tools run, we can refresh the Session Brief
and rank memory candidates so the *next* turn already has a warm envelope.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending: dict[str, bool] = {}
# Single worker queue — one background prep at a time (avoid registry thrash)
_queue: list[dict[str, Any]] = []
_worker_running = False


def schedule_speculative_prep(
    *,
    session_id: str | None,
    brief: Any | None,
    messages: list[dict[str, Any]] | None,
    user_text: str = "",
    project_path: str | None = None,
    memory: Any | None = None,
) -> None:
    """Fire-and-forget prep. Debounced per session; single worker queue."""
    sid = (session_id or "").strip() or "_default"
    with _lock:
        if _pending.get(sid):
            return
        _pending[sid] = True
        _queue.append(
            {
                "session_id": sid,
                "brief": brief,
                "messages": messages,
                "user_text": user_text,
                "project_path": project_path,
                "memory": memory,
            }
        )
        start = not _worker_running

    if start:
        t = threading.Thread(target=_worker_loop, name="remedy-speculative-worker", daemon=True)
        t.start()


def _worker_loop() -> None:
    global _worker_running
    with _lock:
        _worker_running = True
    try:
        while True:
            with _lock:
                if not _queue:
                    _worker_running = False
                    return
                job = _queue.pop(0)
            sid = job["session_id"]
            try:
                _prep(
                    session_id=sid,
                    brief=job.get("brief"),
                    messages=job.get("messages"),
                    user_text=job.get("user_text") or "",
                    project_path=job.get("project_path"),
                    memory=job.get("memory"),
                )
            except Exception:
                logger.debug("speculative prep failed", exc_info=True)
            finally:
                with _lock:
                    _pending[sid] = False
    finally:
        with _lock:
            _worker_running = False


def _prep(
    *,
    session_id: str,
    brief: Any | None,
    messages: list[dict[str, Any]] | None,
    user_text: str,
    project_path: str | None,
    memory: Any | None,
) -> None:
    # 1) Refresh brief from recent messages (cheap)
    if brief is not None and messages:
        try:
            from remedy.memory.harness.compressor import heuristic_merge_from_history

            heuristic_merge_from_history(brief, messages[-24:], intent_hint=user_text or None)
        except Exception:
            pass

    # 2) Stage memory candidates (FTS) for next turn injection if store available
    if memory is not None and (user_text or "").strip():
        try:
            q = (user_text or "").strip()[:200]
            if hasattr(memory, "search"):
                hits = memory.search(q, limit=5)
            elif hasattr(memory, "search_entries"):
                hits = memory.search_entries(q, limit=5)
            else:
                hits = None
            if hits:
                # Stash on brief notes lightly (bounded)
                if brief is not None and hasattr(brief, "notes"):
                    titles = []
                    for h in list(hits)[:3]:
                        t = getattr(h, "title", None) or (
                            h.get("title") if isinstance(h, dict) else None
                        )
                        if t:
                            titles.append(str(t)[:80])
                    if titles:
                        note = "Related memory: " + "; ".join(titles)
                        prev = (brief.notes or "")[:1500]
                        if note not in prev:
                            brief.notes = (prev + "\n" + note).strip()[:2000]
                            if hasattr(brief, "touch"):
                                brief.touch()
        except Exception:
            logger.debug("speculative memory search failed", exc_info=True)

    # 3) Touch project profile lightly (session still open — just heartbeat)
    if project_path:
        try:
            from remedy.core.project_learning import load_project_profile

            load_project_profile(project_path)  # ensure file exists
        except Exception:
            pass

    # 4) Warm skill ranking cache (shared registry — no re-discover thrash)
    try:
        from remedy.nanoswarm import get_swarm
        from remedy.skills.shared import get_shared_registry

        reg = get_shared_registry()
        get_swarm().skill.rank_catalog_lines(reg, limit=24)
    except Exception:
        logger.debug("speculative skill rank failed", exc_info=True)

    # 5) Token nanobot: keep calibrator warm with last message window size
    try:
        from remedy.nanoswarm.token_nanobot import get_token_nanobot

        if messages:
            get_token_nanobot().measure_messages(messages[-16:])
    except Exception:
        pass

    # 6) Scout warm-up: cheap list_dir + git for project (background, local only)
    if project_path:
        try:
            from remedy.nanoswarm import get_swarm

            get_swarm().scout.schedule_warm(project_path, user_text=user_text or "")
        except Exception:
            logger.debug("speculative scout warm failed", exc_info=True)
