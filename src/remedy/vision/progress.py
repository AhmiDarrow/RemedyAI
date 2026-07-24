"""In-process install progress for the visual decoder."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {
    # idle | downloading | extracting | verifying | ready | error | cancelled | uninstalling
    "phase": "idle",
    "message": "",
    "bytes_done": 0,
    "bytes_total": 0,
    "current_file": "",
    "error": None,
    "started_at": None,
    "finished_at": None,
    "model_id": None,
    "runtime_id": None,
    "cancellable": False,
    "resumed": False,
}


def snapshot() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def reset() -> None:
    with _lock:
        _state.update(
            {
                "phase": "idle",
                "message": "",
                "bytes_done": 0,
                "bytes_total": 0,
                "current_file": "",
                "error": None,
                "started_at": None,
                "finished_at": None,
                "model_id": None,
                "runtime_id": None,
                "cancellable": False,
                "resumed": False,
            }
        )


def update(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)


def begin(
    model_id: str,
    runtime_id: str,
    bytes_total: int,
    *,
    bytes_done: int = 0,
    resumed: bool = False,
) -> None:
    with _lock:
        _state.update(
            {
                "phase": "downloading",
                "message": "Resuming install…" if resumed else "Starting install…",
                "bytes_done": int(bytes_done),
                "bytes_total": int(bytes_total),
                "current_file": "",
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
                "model_id": model_id,
                "runtime_id": runtime_id,
                "cancellable": True,
                "resumed": bool(resumed),
            }
        )


def fail(message: str) -> None:
    with _lock:
        _state["phase"] = "error"
        _state["error"] = message
        _state["message"] = message
        _state["finished_at"] = time.time()
        _state["cancellable"] = False


def cancelled(message: str = "Install cancelled — partial downloads kept for resume") -> None:
    with _lock:
        _state["phase"] = "cancelled"
        _state["error"] = None
        _state["message"] = message
        _state["finished_at"] = time.time()
        _state["cancellable"] = False


def succeed(message: str = "Visual decoder ready") -> None:
    with _lock:
        _state["phase"] = "ready"
        _state["message"] = message
        _state["error"] = None
        _state["finished_at"] = time.time()
        _state["cancellable"] = False
