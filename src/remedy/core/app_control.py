"""App control — Remedy driving her own interface (split-second, in-house).

The computer-use tools drive the *world* (browser, desktop apps). This bus
drives Remedy's *own* app: switch surface (Grove ⇄ Studio), open a goal,
focus the composer, open settings. These are things she should do inside
herself instantly, without asking the user to click.

Mechanism mirrors the browser ``ui_command`` bus: the agent enqueues a
command (via the ``app_control`` tool); the desktop/web client polls
``GET /api/app/command?take=1`` on a fast interval and dispatches it. A
tiny FIFO queue (single-user desktop) with a cap so nothing accumulates.

Commands are declarative and safe by construction — they only move the
owner's own UI around; nothing here spends money, touches files, or leaves
the app.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

# Actions the client knows how to dispatch. Keep in lockstep with the
# frontend dispatcher (api/appControl.ts + App.tsx).
VALID_ACTIONS = frozenset(
    {
        "switch_surface",   # target: "grove" | "studio"
        "open_goal",        # goal_id: str
        "focus_composer",   # (no args)
        "open_settings",    # section?: str
        "open_panel",       # panel: str (studio right-rail panel id)
        "new_session",      # (no args)
    }
)
_MAX_QUEUE = 32
_MAX_AGE_S = 90.0


class _AppControlBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._q: deque[dict[str, Any]] = deque(maxlen=_MAX_QUEUE)
        self._seq = 0

    def enqueue(self, action: str, **params: Any) -> dict[str, Any]:
        act = (action or "").strip()
        if act not in VALID_ACTIONS:
            return {"ok": False, "error": f"unknown app action {act!r}"}
        with self._lock:
            self._seq += 1
            cmd = {
                "id": f"app-{self._seq}",
                "action": act,
                "params": {k: v for k, v in params.items() if v is not None},
                "ts": time.time(),
            }
            self._q.append(cmd)
        return {"ok": True, "command": cmd}

    def _prune(self) -> None:
        now = time.time()
        while self._q and (now - float(self._q[0].get("ts") or 0)) > _MAX_AGE_S:
            self._q.popleft()

    def peek(self) -> dict[str, Any] | None:
        with self._lock:
            self._prune()
            return dict(self._q[0]) if self._q else None

    def take(self) -> dict[str, Any] | None:
        with self._lock:
            self._prune()
            return dict(self._q.popleft()) if self._q else None

    def clear(self) -> None:
        with self._lock:
            self._q.clear()


_bus = _AppControlBus()


def app_control_bus() -> _AppControlBus:
    return _bus


def request_app_action(action: str, **params: Any) -> dict[str, Any]:
    """Enqueue one app-control command for the client to dispatch."""
    return _bus.enqueue(action, **params)
