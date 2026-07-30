"""Full in-place session reset — empty slate, same session id.

Unlike ``/new`` (creates another chat tab), this wipes history and session-scoped
context for the *current* session so the next turn behaves like a brand-new chat.
Does **not** wipe durable Partner Memory (``/remember`` facts) or global config.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _home(runtime: Any = None) -> Path:
    home: Path | str | None = None
    if runtime is not None:
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            home = getattr(runtime, "home_dir", None)
    if not home:
        with contextlib.suppress(Exception):
            from remedy.interfaces.config import load_config

            home = load_config().get("home_dir")
    if home:
        return Path(home).expanduser()
    return Path.home() / ".remedy"


def _purge_session_plans(session_id: str, home: Path) -> int:
    from remedy.core.plan_store import PlanStore

    store = PlanStore(home)
    n = 0
    for plan in store.list_plans(session_id=str(session_id), limit=500):
        path = store._path(plan.id)
        with contextlib.suppress(OSError):
            if path.is_file():
                path.unlink()
                n += 1
    return n


def _purge_session_checkpoints(session_id: str, home: Path) -> int:
    from remedy.core.checkpoint import CheckpointStore

    store = CheckpointStore(home)
    n = 0
    for cp in store.list_for_session(str(session_id), limit=500):
        path = store._path(cp.id)
        with contextlib.suppress(OSError):
            if path.is_file():
                path.unlink()
                n += 1
    return n


def _purge_attachments(session_id: str, home: Path) -> bool:
    from remedy.interfaces.attachments import session_attachments_dir

    d = session_attachments_dir(session_id, home)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def _purge_runtime_state(session_id: str, runtime: Any) -> None:
    """Clear process-local state for *this session only* (shared BasicRuntime)."""
    sid = str(session_id)
    # Streaming marker
    with contextlib.suppress(Exception):
        streams = getattr(runtime, "_streaming_sessions", None)
        if isinstance(streams, set):
            streams.discard(sid)

    # Session Brief: registry is session-keyed; only null the live slot if it
    # belongs to this session (desktop shares one BasicRuntime across tabs).
    with contextlib.suppress(Exception):
        brief = getattr(runtime, "_session_brief", None)
        if brief is not None:
            bsid = str(getattr(brief, "session_id", "") or "")
            rt_sid = str(getattr(runtime, "_session_id", "") or "")
            if bsid == sid or (not bsid and rt_sid == sid):
                runtime._session_brief = None  # type: ignore[attr-defined]
    with contextlib.suppress(Exception):
        from remedy.memory.harness.local_brief import _brief_registry, _brief_registry_lock

        with _brief_registry_lock:
            _brief_registry.pop(sid, None)
    # Continuity process cache (tab rebind stash) — drop this session so a
    # later rebound cannot resurrect a wiped brief / work roots.
    with contextlib.suppress(Exception):
        from remedy.core.session_continuity import drop_session_continuity_cache

        drop_session_continuity_cache(sid)

    # Partner State registry (subgoals / txns / graph) — drop this session only
    with contextlib.suppress(Exception):
        from remedy.memory.partner_state.state import _registry, _registry_lock

        with _registry_lock:
            _registry.pop(sid, None)
        rt_sid = str(getattr(runtime, "_session_id", "") or "")
        if rt_sid == sid and hasattr(runtime, "_partner_state"):
            runtime._partner_state = None  # type: ignore[attr-defined]
        if hasattr(runtime, "_prospective_session_fired") and rt_sid == sid:
            runtime._prospective_session_fired = False

    # Turn scratch: only clear if this runtime is currently bound to *sid*
    # (never wipe another tab's in-flight turn buffers).
    with contextlib.suppress(Exception):
        rt_sid = str(getattr(runtime, "_session_id", "") or "")
        if rt_sid == sid:
            for attr in (
                "_turn_tool_steps",
                "_last_tool_steps",
                "_pending_tool_results",
                "_stream_accum",
            ):
                if hasattr(runtime, attr):
                    val = getattr(runtime, attr)
                    if isinstance(val, (list, dict, set)):
                        val.clear()
                    else:
                        setattr(runtime, attr, None)

    # In-memory goals/tasks (dict[UUID, Task]): only drop those tagged for this
    # session in metadata.session_id. Never clear the whole map (multi-tab).
    with contextlib.suppress(Exception):
        tasks = getattr(runtime, "_tasks", None)
        if isinstance(tasks, dict):
            drop_keys: list[Any] = []
            for k, v in list(tasks.items()):
                if str(k) == sid:
                    drop_keys.append(k)
                    continue
                meta = None
                if isinstance(v, dict):
                    meta = v.get("metadata") if isinstance(v.get("metadata"), dict) else v
                    if str(v.get("session_id") or "") == sid:
                        drop_keys.append(k)
                        continue
                else:
                    meta = getattr(v, "metadata", None)
                    if str(getattr(v, "session_id", "") or "") == sid:
                        drop_keys.append(k)
                        continue
                if isinstance(meta, dict) and str(meta.get("session_id") or "") == sid:
                    drop_keys.append(k)
            for k in drop_keys:
                tasks.pop(k, None)
            # When this runtime is currently bound to sid, also drop untagged
            # tasks (legacy Task objects with no session metadata) — only if
            # no other session is co-using untagged tasks is unknowable, so we
            # only clear untagged when rt is bound to this session AND the
            # streaming set has only this sid (single active session).
            rt_sid = str(getattr(runtime, "_session_id", "") or "")
            streams = getattr(runtime, "_streaming_sessions", None)
            solo = isinstance(streams, set) and (not streams or streams == {sid})
            if rt_sid == sid and solo:
                for k, v in list(tasks.items()):
                    meta = getattr(v, "metadata", None) if not isinstance(v, dict) else v.get("metadata")
                    if not isinstance(meta, dict) or not str(meta.get("session_id") or "").strip():
                        # untagged legacy task — clear on solo-session reset
                        if not isinstance(v, dict) or "session_id" not in v:
                            if not hasattr(v, "session_id") or not getattr(v, "session_id", None):
                                if k not in drop_keys:
                                    tasks.pop(k, None)

    # Nanoswarm pattern buffer for this session
    with contextlib.suppress(Exception):
        pattern = getattr(runtime, "_pattern_nanobot", None) or getattr(
            runtime, "pattern_nanobot", None
        )
        if pattern is not None and hasattr(pattern, "clear_session"):
            pattern.clear_session(sid)


async def full_reset_session(
    session_id: str,
    memory: Any,
    *,
    runtime: Any = None,
    home_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Wipe session-scoped state; keep the same session id.

    Returns a stats dict for logging / UI text.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "no session_id"}

    home = Path(home_dir).expanduser() if home_dir else _home(runtime)
    stats: dict[str, Any] = {
        "ok": True,
        "session_id": sid,
        "messages": 0,
        "plans": 0,
        "checkpoints": 0,
        "attachments_purged": False,
        "title_reset": False,
    }

    # 1) Chat history + session summary row
    if memory is not None and hasattr(memory, "clear_chat_messages"):
        try:
            stats["messages"] = int(await memory.clear_chat_messages(sid) or 0)
        except Exception as exc:
            logger.warning("clear_chat_messages failed: %s", exc)
            stats["ok"] = False
            stats["error"] = str(exc)
            return stats

    # 2) Title → New Session (keep project_path / model / provider)
    if memory is not None and hasattr(memory, "update_chat_session"):
        with contextlib.suppress(Exception):
            await memory.update_chat_session(sid, title="New Session")
            stats["title_reset"] = True

    # 3) Session-scoped memory entries (not global Partner Memory / user facts)
    if memory is not None and hasattr(memory, "delete_by_session"):
        with contextlib.suppress(Exception):
            stats["memory_entries"] = int(await memory.delete_by_session(sid) or 0)
    elif memory is not None:
        with contextlib.suppress(Exception):
            entries = await memory.list_by_session(sid, limit=500)
            deleted = 0
            for e in entries or []:
                eid = getattr(e, "id", None)
                if eid is not None and hasattr(memory, "delete"):
                    if await memory.delete(eid):
                        deleted += 1
            stats["memory_entries"] = deleted

    # 4) Filesystem: plans, checkpoints, attachments
    with contextlib.suppress(Exception):
        stats["plans"] = _purge_session_plans(sid, home)
    with contextlib.suppress(Exception):
        stats["checkpoints"] = _purge_session_checkpoints(sid, home)
    with contextlib.suppress(Exception):
        stats["attachments_purged"] = _purge_attachments(sid, home)

    # 5) Process-local caches
    with contextlib.suppress(Exception):
        from remedy.core.session_quality import reset_session_quality

        reset_session_quality(sid)
    with contextlib.suppress(Exception):
        from remedy.skills.library.suggest import clear_session_suppress

        clear_session_suppress(sid)
    with contextlib.suppress(Exception):
        from remedy.core.turn_context import abort_session as _abort_turn

        _abort_turn(sid)

    if runtime is not None:
        _purge_runtime_state(sid, runtime)

    # 6) Vision decode cache is global; leave it (image bytes only, not chat context)

    return stats
