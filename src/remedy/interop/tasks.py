"""Durable A2A task store in {home}/interop/tasks.json."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.core.organism_knobs import resolve_int
from remedy.interop.paths import tasks_path

# A2A v1.0 TaskState (ProtoJSON SCREAMING_SNAKE_CASE).
SUBMITTED = "TASK_STATE_SUBMITTED"
WORKING = "TASK_STATE_WORKING"
COMPLETED = "TASK_STATE_COMPLETED"
FAILED = "TASK_STATE_FAILED"
CANCELED = "TASK_STATE_CANCELED"
REJECTED = "TASK_STATE_REJECTED"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"

TERMINAL = frozenset({COMPLETED, FAILED, CANCELED, REJECTED})


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def empty_store() -> dict[str, Any]:
    return {"version": 1, "tasks": {}}


def load(home: str | None = None) -> dict[str, Any]:
    path = tasks_path(home)
    if not path.is_file():
        return empty_store()
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_store()
    if not isinstance(data, dict):
        return empty_store()
    if not isinstance(data.get("tasks"), dict):
        data["tasks"] = {}
    data.setdefault("version", 1)
    return data


def save(store: dict[str, Any], home: str | None = None) -> None:
    path = tasks_path(home)
    write_json_atomic(path, {"version": 1, "tasks": store.get("tasks") or {}})


def new_id() -> str:
    return str(uuid.uuid4())


def create(
    store: dict[str, Any],
    *,
    home: str | None = None,
    message: dict[str, Any] | None = None,
    context_id: str | None = None,
    peer_id: str | None = None,
    action: str = "ask",
    state: str = SUBMITTED,
    metadata: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _purge_expired(store, cfg)
    task_id = new_id()
    ctx = context_id or new_id()
    status_message = message
    task: dict[str, Any] = {
        "id": task_id,
        "contextId": ctx,
        "status": {"state": state, "timestamp": now_iso()},
        "history": [message] if message else [],
        "artifacts": [],
        "metadata": {
            "peer_id": peer_id,
            "action": action,
            "created_ts": time.time(),
            **(metadata or {}),
        },
    }
    if status_message:
        task["status"]["message"] = status_message
    store.setdefault("tasks", {})[task_id] = task
    save(store, home)
    return task


def get(store: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    item = (store.get("tasks") or {}).get(task_id)
    return dict(item) if isinstance(item, dict) else None


def list_tasks(
    store: dict[str, Any],
    *,
    page_size: int = 20,
    page_token: str | None = None,
    state: str | None = None,
    context_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    items = [dict(t) for t in (store.get("tasks") or {}).values() if isinstance(t, dict)]
    items.sort(key=lambda t: str((t.get("status") or {}).get("timestamp") or ""), reverse=True)
    if state:
        items = [t for t in items if (t.get("status") or {}).get("state") == state]
    if context_id:
        items = [t for t in items if t.get("contextId") == context_id]
    start = 0
    if page_token:
        for i, item in enumerate(items):
            if item.get("id") == page_token:
                start = i + 1
                break
    page_size = max(1, min(int(page_size or 20), 100))
    page = items[start : start + page_size]
    nxt = page[-1]["id"] if len(items) > start + page_size and page else None
    return page, nxt


def update_state(
    store: dict[str, Any],
    task_id: str,
    state: str,
    *,
    home: str | None = None,
    message: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    task = (store.get("tasks") or {}).get(task_id)
    if not isinstance(task, dict):
        return None
    task["status"] = {"state": state, "timestamp": now_iso()}
    if message:
        task["status"]["message"] = message
        history = list(task.get("history") or [])
        history.append(message)
        task["history"] = history[-50:]
    if artifact:
        arts = list(task.get("artifacts") or [])
        arts.append(artifact)
        task["artifacts"] = arts
    if extra_metadata:
        meta = dict(task.get("metadata") or {})
        meta.update(extra_metadata)
        task["metadata"] = meta
    store["tasks"][task_id] = task
    save(store, home)
    return task


def cancel(store: dict[str, Any], task_id: str, *, home: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    task = get(store, task_id)
    if task is None:
        return None, "not_found"
    state = (task.get("status") or {}).get("state")
    if state in TERMINAL:
        return task, "not_cancelable"
    updated = update_state(store, task_id, CANCELED, home=home)
    return updated, None


def _purge_expired(store: dict[str, Any], cfg: dict[str, Any] | None) -> None:
    ttl = resolve_int("interop_task_ttl_s", cfg)
    now = time.time()
    tasks = store.get("tasks") or {}
    drop = []
    for tid, task in tasks.items():
        if not isinstance(task, dict):
            drop.append(tid)
            continue
        created = float((task.get("metadata") or {}).get("created_ts") or 0)
        state = (task.get("status") or {}).get("state")
        if created and now - created > ttl and state in TERMINAL:
            drop.append(tid)
    for tid in drop:
        tasks.pop(tid, None)
