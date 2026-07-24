"""Pending tool-approval queue for high-impact actions (partner trust loop)."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

# Soft-ask (not hard-block): user can approve once / session / always-pattern
_ASK_PATTERNS = re.compile(
    r"(?is)"
    r"("
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|--force\b).{0,40}\S"
    r"|\bdel\s+/[sqf]"
    r"|\brmdir\s+/s"
    r"|\bRemove-Item\b.{0,80}(-Recurse|-Force)"
    r"|\bformat\s+[a-z]:"
    r"|\b(reg\s+delete|takeown\b|icacls\b.{0,40}/grant)"
    r"|\b(drop\s+database|truncate\s+table)\b"
    r"|\b(git\s+push\s+--force|git\s+reset\s+--hard)\b"
    r")"
)


@dataclass
class PendingApproval:
    id: str
    tool_name: str
    command: str
    reason: str
    session_id: str | None = None
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | approved | denied
    fingerprint: str = ""


class ApprovalQueue:
    """Process-local approval queue (desktop + CLI session)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, PendingApproval] = {}
        # Approved fingerprints for this process lifetime
        self._approved_fps: set[str] = set()
        # Session-scoped approvals
        self._session_fps: dict[str, set[str]] = {}
        # ask (default) | auto — status-bar thumbs toggle
        self._mode: str = "ask"

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> str:
        """Set approval mode: ``ask`` (thumbs down) or ``auto`` (thumbs up)."""
        m = (mode or "ask").strip().lower()
        if m not in ("ask", "auto"):
            m = "ask"
        with self._lock:
            self._mode = m
            return self._mode

    @staticmethod
    def fingerprint(tool_name: str, command: str) -> str:
        return f"{tool_name}::{(command or '').strip()}"

    def needs_ask(self, command: str) -> str | None:
        """Return reason string if command should require approval.

        When mode is ``auto`` (status-bar thumbs-up), skip prompts entirely.
        """
        with self._lock:
            if self._mode == "auto":
                return None
        c = (command or "").strip()
        if not c:
            return None
        if _ASK_PATTERNS.search(c):
            return "High-impact / destructive command pattern"
        return None

    def is_approved(self, tool_name: str, command: str, session_id: str | None = None) -> bool:
        fp = self.fingerprint(tool_name, command)
        with self._lock:
            if fp in self._approved_fps:
                return True
            if session_id and fp in self._session_fps.get(session_id, set()):
                return True
        return False

    def create(
        self,
        *,
        tool_name: str,
        command: str,
        reason: str,
        session_id: str | None = None,
    ) -> PendingApproval:
        item = PendingApproval(
            id=uuid4().hex[:12],
            tool_name=tool_name,
            command=command,
            reason=reason,
            session_id=session_id,
            fingerprint=self.fingerprint(tool_name, command),
        )
        with self._lock:
            self._items[item.id] = item
            # Prune old pending (>1h)
            cutoff = time.time() - 3600
            dead = [k for k, v in self._items.items() if v.created_at < cutoff]
            for k in dead:
                del self._items[k]
        return item

    def get(self, approval_id: str) -> PendingApproval | None:
        with self._lock:
            return self._items.get(approval_id)

    def list_pending(self, session_id: str | None = None) -> list[PendingApproval]:
        with self._lock:
            items = [v for v in self._items.values() if v.status == "pending"]
            if session_id:
                items = [v for v in items if v.session_id in (None, session_id)]
            return sorted(items, key=lambda x: x.created_at, reverse=True)

    def resolve(
        self,
        approval_id: str,
        *,
        approve: bool,
        scope: str = "session",
    ) -> PendingApproval | None:
        with self._lock:
            item = self._items.get(approval_id)
            if not item or item.status != "pending":
                return item
            item.status = "approved" if approve else "denied"
            if approve:
                if scope == "always":
                    self._approved_fps.add(item.fingerprint)
                else:
                    sid = item.session_id or "default"
                    self._session_fps.setdefault(sid, set()).add(item.fingerprint)
            return item

    def to_public(self, item: PendingApproval) -> dict[str, Any]:
        return {
            "id": item.id,
            "tool_name": item.tool_name,
            "command": item.command[:500],
            "reason": item.reason,
            "session_id": item.session_id,
            "status": item.status,
            "created_at": item.created_at,
        }


# Singleton used by agent + API
APPROVALS = ApprovalQueue()
