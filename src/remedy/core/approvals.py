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

    _MAX_APPROVED_FP = 1000
    _MAX_SESSIONS = 48

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, PendingApproval] = {}
        # Approved fingerprints for this process lifetime
        self._approved_fps: set[str] = set()
        # Session-scoped approvals
        self._session_fps: dict[str, set[str]] = {}
        self._session_order: list[str] = []
        # ask (default) | auto — status-bar thumbs toggle
        self._mode: str = "ask"

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, mode: str) -> str:
        """Set approval mode: ``ask`` (thumbs down) or ``auto`` (thumbs up).

        Switching to **auto** also clears any pending prompts (full owner power).
        """
        m = (mode or "ask").strip().lower()
        if m not in ("ask", "auto"):
            m = "ask"
        with self._lock:
            prev = self._mode
            self._mode = m
            if m == "auto" and prev != "auto":
                # Auto-approve anything still waiting so the banner disappears
                # and in-flight tool retries can proceed without a click.
                for item in self._items.values():
                    if item.status == "pending":
                        item.status = "approved"
                        sid = item.session_id or "default"
                        self._add_session_fp(sid, item.fingerprint)
            return self._mode

    def sync_from_config(self, cfg: dict[str, Any] | None = None) -> str:
        """Align process mode with persisted config.toml when explicitly set.

        Fixes the restart bug: Settings UI read ``approval_mode=auto`` from TOML
        while the in-memory queue stayed on ``ask`` because AgentConfig omitted
        the field at boot.

        Only overrides mode when *cfg* contains ``approval_mode`` so unit tests
        that call ``set_mode("ask")`` with a partial monkeypatched config keep
        their intended mode.
        """
        if cfg is None:
            try:
                from remedy.interfaces.api_support import load_config

                cfg = load_config() or {}
            except Exception:
                cfg = {}
        if not isinstance(cfg, dict) or "approval_mode" not in cfg:
            with self._lock:
                return self._mode
        am = str(cfg.get("approval_mode") or "ask").strip().lower()
        if am not in ("ask", "auto"):
            am = "ask"
        return self.set_mode(am)

    @staticmethod
    def fingerprint(tool_name: str, command: str) -> str:
        return f"{tool_name}::{(command or '').strip()}"

    def _add_session_fp(self, session_id: str, fp: str) -> None:
        """Add a fingerprint to a session, evicting oldest sessions when over capacity."""
        self._session_fps.setdefault(session_id, set()).add(fp)
        if session_id not in self._session_order:
            self._session_order.append(session_id)
        while len(self._session_fps) > self._MAX_SESSIONS:
            old_sid = self._session_order.pop(0)
            self._session_fps.pop(old_sid, None)

    def _trim_approved_fps(self) -> None:
        """Evict oldest approved fingerprints when over capacity."""
        while len(self._approved_fps) > self._MAX_APPROVED_FP:
            self._approved_fps.pop()

    # Tools that always require approval in ``ask`` mode (not only pattern match).
    # ``auto`` mode skips these on trusted scopes so "work until done" has full power.
    # Computer mutation tools are high-impact (OS click/type/launch) — same bar as shell.
    HIGH_IMPACT_TOOLS = frozenset(
        {
            "bash_exec",
            "file_write",
            "file_edit",
            "skill_run",
            "mail_send",
            "computer_click",
            "computer_type",
            "computer_key",
            "computer_drag",
            "computer_act",
            "computer_app",
        }
    )

    def needs_ask(self, command: str, *, tool_name: str = "") -> str | None:
        """Return reason string if action should require approval.

        **Power model (never stripped for the owner):**
        - ``ask`` (default, safe): high-impact tools + risk patterns prompt.
        - ``auto`` (status-bar thumbs-up / work-until-done): no prompts on a
          normal trusted scope — Remedy runs shell/write/skills to finish.
        - ``untrusted`` access scope still always asks (downloaded folders).
        Hard security blocks (wipe/privilege) live in ``check_dangerous_command``
        and are separate from this partner-trust queue.
        """
        # Re-sync mode when config explicitly sets approval_mode (desktop thumbs).
        # Scope always comes from config when available.
        untrusted = False
        try:
            from remedy.interfaces.api_support import load_config

            cfg = load_config() or {}
            if isinstance(cfg, dict) and "approval_mode" in cfg:
                self.sync_from_config(cfg)
            scope = str(cfg.get("access_scope") or "").lower()
            untrusted = scope in ("untrusted", "sandbox", "strict", "download")
        except Exception:
            untrusted = False
        with self._lock:
            if self._mode == "auto" and not untrusted:
                return None
        tool = (tool_name or "").strip()
        c = (command or "").strip()
        reason: str | None = None
        soft: str | None = None
        # Soft-risk signals (advisory) for clearer Ask banners — not hard blocks.
        try:
            from remedy.core.security import check_soft_dangerous_command

            soft = check_soft_dangerous_command(["bash", "-c", c] if c else [])
        except Exception:
            soft = None
        if untrusted and tool in self.HIGH_IMPACT_TOOLS:
            reason = "Untrusted workspace — approval required"
        if tool in self.HIGH_IMPACT_TOOLS and not reason:
            if tool == "bash_exec":
                reason = "Shell execution requires approval (bash_exec)"
            elif tool == "file_write":
                reason = "File write requires approval (file_write)"
            elif tool == "file_edit":
                reason = "File edit requires approval (file_edit)"
            elif tool == "skill_run":
                reason = "Skill script execution requires approval (skill_run)"
            elif tool == "mail_send":
                reason = "Sending email requires approval (mail_send)"
            elif tool.startswith("computer_"):
                reason = f"Computer control requires approval ({tool})"
        if not reason and c and _ASK_PATTERNS.search(c):
            reason = "High-impact / destructive command pattern"
        if reason and soft:
            reason = f"{reason} · soft-risk: {soft}"
        elif soft and not reason and tool == "bash_exec":
            # Soft-only: still ask in ask-mode for bash with risk signals
            reason = f"Soft-risk shell pattern — confirm to proceed ({soft})"
        # Guard nanobot: enrich reason with risk score (never hard-blocks here)
        try:
            from remedy.nanoswarm import get_swarm

            reason = get_swarm().guard.enrich_ask_reason(
                reason,
                tool_name=tool,
                command=c,
            )
        except Exception:
            pass
        return reason

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
                    self._trim_approved_fps()
                else:
                    sid = item.session_id or "default"
                    self._add_session_fp(sid, item.fingerprint)
            return item

    def to_public(self, item: PendingApproval) -> dict[str, Any]:
        soft_risk = None
        if "soft-risk:" in (item.reason or "").lower() or "Soft-risk" in (item.reason or ""):
            soft_risk = item.reason
        return {
            "id": item.id,
            "tool_name": item.tool_name,
            "command": item.command[:500],
            "reason": item.reason,
            "soft_risk": soft_risk,
            "session_id": item.session_id,
            "status": item.status,
            "created_at": item.created_at,
            "approval_mode_hint": (
                "Approve for this session, or set Approvals → Auto to let Remedy "
                "finish work without prompts (full owner power on trusted scope)."
            ),
        }


# Singleton used by agent + API
APPROVALS = ApprovalQueue()
