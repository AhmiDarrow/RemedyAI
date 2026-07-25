"""Goal nanobot — track open goals/plans against tool progress (silent).

Surfaces stuck-on-goal signals for Continuity remedies, not chat theater.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class GoalNanobot:
    """Per-session open goals + lightweight progress against tool steps."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # session_id -> {titles, last_tool, tool_steps_since_goal, completed}
        self._sessions: dict[str, dict[str, Any]] = {}
        self.updates = 0

    def _key(self, session_id: str | None) -> str:
        return (session_id or "").strip() or "_default"

    def _sess(self, session_id: str | None) -> dict[str, Any]:
        k = self._key(session_id)
        with self._lock:
            if k not in self._sessions:
                self._sessions[k] = {
                    "open": [],  # list[str] titles
                    "completed": [],
                    "tool_steps": 0,
                    "last_tool": None,
                    "last_goal_touch": 0.0,
                    "stale": False,
                }
            return self._sessions[k]

    def sync_from_brief(
        self,
        brief: Any | None,
        *,
        session_id: str | None = None,
        runtime: Any | None = None,
    ) -> dict[str, Any]:
        """Pull open tasks/goals from brief and optional runtime task list."""
        s = self._sess(session_id)
        open_titles: list[str] = []
        if brief is not None:
            for t in list(getattr(brief, "open_tasks", None) or [])[:20]:
                ts = str(t).strip()
                if ts and ts not in open_titles:
                    open_titles.append(ts)
        if runtime is not None:
            try:
                from remedy.models import TaskStatus

                for t in list(runtime.list_tasks() or [])[:40]:
                    tags = t.tags or []
                    if "goal" not in tags and not str(getattr(t, "title", "")).strip():
                        continue
                    if getattr(t, "status", None) == TaskStatus.COMPLETED:
                        continue
                    title = str(t.title).strip()
                    if title and title not in open_titles:
                        open_titles.append(title)
            except Exception:
                pass
        with self._lock:
            s["open"] = open_titles[:16]
            s["last_goal_touch"] = time.time()
            self.updates += 1
        return self.snapshot(session_id)

    def on_tool_step(
        self,
        tool_name: str,
        *,
        success: bool = True,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        s = self._sess(session_id)
        with self._lock:
            s["tool_steps"] = int(s.get("tool_steps") or 0) + 1
            s["last_tool"] = tool_name
            open_n = len(s.get("open") or [])
            # Stale: many tools while goals sit open and no goal_complete
            s["stale"] = open_n > 0 and s["tool_steps"] >= 8 and success
            self.updates += 1
        return self.snapshot(session_id)

    def note_completed(self, title: str, *, session_id: str | None = None) -> None:
        s = self._sess(session_id)
        t = (title or "").strip()
        if not t:
            return
        with self._lock:
            s["open"] = [x for x in (s.get("open") or []) if x.lower() != t.lower()]
            done = list(s.get("completed") or [])
            done.append(t)
            s["completed"] = done[-20:]
            s["tool_steps"] = 0
            s["stale"] = False

    def system_hint(self, session_id: str | None = None) -> str:
        snap = self.snapshot(session_id)
        open_g = snap.get("open") or []
        if not open_g:
            return ""
        lines = ", ".join(open_g[:5])
        if snap.get("stale"):
            return (
                f"[Continuity/Goal] Open goals still pending ({lines}). "
                "Prefer progress toward them or goal_complete with evidence; "
                "avoid tool thrash unrelated to the goal."
            )
        return f"[Continuity/Goal] Active goals: {lines}."

    def snapshot(self, session_id: str | None = None) -> dict[str, Any]:
        s = self._sess(session_id)
        with self._lock:
            return {
                "bot": "goal",
                "session_id": self._key(session_id),
                "open": list(s.get("open") or []),
                "completed_recent": list(s.get("completed") or [])[-5:],
                "tool_steps_since_sync": int(s.get("tool_steps") or 0),
                "last_tool": s.get("last_tool"),
                "stale": bool(s.get("stale")),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            n = len(self._sessions)
            open_total = sum(len(s.get("open") or []) for s in self._sessions.values())
        return {
            "bot": "goal",
            "sessions": n,
            "open_goals_total": open_total,
            "updates": self.updates,
        }
