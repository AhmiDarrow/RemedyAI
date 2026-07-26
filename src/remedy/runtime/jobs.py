"""Serialized job queue for the shared llama-server (vision | nano | helper)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from remedy.runtime.roles import LocalRole


@dataclass
class LocalJob:
    """One unit of work on the shared local model."""

    role: LocalRole
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    priority: int = 0  # higher runs first within queue
    # Handler snapshot at submit time — avoids late-bound re-register races
    _handler: Callable[[LocalJob], Any] | None = field(
        default=None, repr=False, compare=False
    )

    def to_public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "role": self.role.value,
            "kind": self.kind,
            "priority": self.priority,
            "created_at": self.created_at,
        }


class LocalJobQueue:
    """In-process exclusive queue so vision and nano do not thrash one server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: list[LocalJob] = []
        self._current: LocalJob | None = None
        self._handlers: dict[str, Callable[[LocalJob], Any]] = {}
        self._worker_started = False

    def register(self, kind: str, handler: Callable[[LocalJob], Any]) -> None:
        with self._lock:
            self._handlers[kind] = handler

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker_started:
                return
            self._worker_started = True

        def _loop() -> None:
            while True:
                with self._cond:
                    while not self._pending:
                        self._cond.wait()
                    job = self._pending.pop(0)
                    self._current = job
                    done_event: threading.Event = job.payload.setdefault(  # type: ignore[assignment]
                        "_done_event", threading.Event()
                    )
                    result_box: dict[str, Any] = job.payload.setdefault("_result_box", {})
                    handler = job._handler or self._handlers.get(job.kind)
                try:
                    if handler is None:
                        result_box["error"] = f"No handler for job kind {job.kind!r}"
                    else:
                        result_box["result"] = handler(job)
                except Exception as e:
                    result_box["error"] = str(e)
                finally:
                    with self._cond:
                        if self._current and self._current.job_id == job.job_id:
                            self._current = None
                        self._cond.notify_all()
                    done_event.set()

        t = threading.Thread(target=_loop, name="remedy-local-job-queue", daemon=True)
        t.start()

    def submit(self, job: LocalJob, *, wait: bool = True, timeout: float = 120.0) -> Any:
        """Enqueue job; optionally wait for result (exclusive execution)."""
        done = threading.Event()
        box: dict[str, Any] = {}
        job.payload["_done_event"] = done
        job.payload["_result_box"] = box
        self._ensure_worker()
        with self._cond:
            # Snapshot handler under lock so re-register mid-flight cannot rebind
            if job._handler is None:
                job._handler = self._handlers.get(job.kind)
            # Cap queue depth — drop lowest-priority oldest if flooded
            if len(self._pending) >= 32:
                self._pending.sort(key=lambda j: (j.priority, -j.created_at))
                dropped = self._pending.pop(0)
                logger = __import__("logging").getLogger(__name__)
                logger.warning(
                    "Local job queue full; dropped job %s kind=%s",
                    dropped.job_id,
                    dropped.kind,
                )
            self._pending.append(job)
            self._pending.sort(key=lambda j: (-j.priority, j.created_at))
            self._cond.notify()
        if not wait:
            return {"job_id": job.job_id, "queued": True}
        if not done.wait(timeout=timeout):
            return {
                "ok": False,
                "error": "Local job timed out",
                "job_id": job.job_id,
                "timed_out": True,
            }
        if "error" in box:
            return {"ok": False, "error": box["error"], "job_id": job.job_id}
        return {"ok": True, "result": box.get("result"), "job_id": job.job_id}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "current": self._current.to_public() if self._current else None,
                "pending": [j.to_public() for j in self._pending],
                "handlers": sorted(self._handlers),
            }


_default_queue: LocalJobQueue | None = None
_queue_lock = threading.Lock()


def default_queue() -> LocalJobQueue:
    global _default_queue
    with _queue_lock:
        if _default_queue is None:
            _default_queue = LocalJobQueue()
        return _default_queue
