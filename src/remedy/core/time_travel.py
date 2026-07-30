"""Session time-travel: undo logs for workspace files + timeline helpers.

When the agent writes files during a chat turn, we record enough state to
restore paths if the user rolls the conversation back to an earlier step.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def undo_root(home_dir: Path | str | None = None) -> Path:
    home = Path(home_dir).expanduser() if home_dir else Path.home() / ".remedy"
    root = home / "undo"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class FileUndoEntry:
    """One reversible filesystem mutation."""

    id: str
    session_id: str
    message_id: str | None
    path: str
    existed: bool
    previous_content: str | None
    new_size: int = 0
    created_at: str = field(default_factory=_now)
    kind: str = "file_write"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FileUndoEntry:
        return cls(
            id=str(raw.get("id") or uuid4().hex[:12]),
            session_id=str(raw.get("session_id") or ""),
            message_id=raw.get("message_id"),
            path=str(raw.get("path") or ""),
            existed=bool(raw.get("existed")),
            previous_content=raw.get("previous_content"),
            new_size=int(raw.get("new_size") or 0),
            created_at=str(raw.get("created_at") or _now()),
            kind=str(raw.get("kind") or "file_write"),
        )


class SessionUndoLog:
    """Append-only JSONL undo log per chat session."""

    # Cap stored previous content so huge files don't bloat the log.
    MAX_PREV_CHARS = 400_000

    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home = Path(home_dir).expanduser() if home_dir else Path.home() / ".remedy"
        self.root = undo_root(self.home)

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", session_id) or "session"
        return self.root / f"{safe}.jsonl"

    def record_file_write(
        self,
        *,
        session_id: str,
        path: Path | str,
        previous_content: str | None,
        existed: bool,
        new_size: int,
        message_id: str | None = None,
    ) -> FileUndoEntry | None:
        if not session_id:
            return None
        prev = previous_content
        if prev is not None and len(prev) > self.MAX_PREV_CHARS:
            # Still mark that the file existed; cannot fully restore oversized.
            prev = prev[: self.MAX_PREV_CHARS] + "\n/* …truncated for undo log */\n"
        entry = FileUndoEntry(
            id=uuid4().hex[:12],
            session_id=session_id,
            message_id=message_id,
            path=str(path),
            existed=existed,
            previous_content=prev if existed else None,
            new_size=int(new_size),
        )
        try:
            p = self._path(session_id)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("undo log write failed: %s", exc)
            return None
        return entry

    def list_entries(self, session_id: str) -> list[FileUndoEntry]:
        p = self._path(session_id)
        if not p.is_file():
            return []
        out: list[FileUndoEntry] = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    out.append(FileUndoEntry.from_dict(raw))
        except OSError:
            return []
        return out

    def purge_session(self, session_id: str) -> bool:
        """Delete the undo JSONL for *session_id* (session delete / reset).

        Prior file bodies must not linger after the chat is gone.
        """
        sid = str(session_id or "").strip()
        if not sid:
            return False
        p = self._path(sid)
        if not p.is_file():
            return False
        try:
            p.unlink()
            return True
        except OSError as exc:
            logger.debug("undo purge failed for %s: %s", sid, exc)
            return False

    def restore_after(
        self,
        session_id: str,
        *,
        cut_created_at: str | None = None,
        cut_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Reverse file writes that happened at/after the cut point.

        Applies in reverse chronological order. Prefer ``cut_created_at`` when
        known (message timestamp); otherwise restore all entries after the
        matching message_id if present.
        """
        entries = self.list_entries(session_id)
        if not entries:
            return {"restored": 0, "deleted": 0, "skipped": 0, "paths": []}

        targets: list[FileUndoEntry]
        if cut_message_id:
            # Restore this message's mutations and everything logged after it.
            start = next(
                (i for i, e in enumerate(entries) if e.message_id == cut_message_id),
                None,
            )
            targets = list(entries[start:]) if start is not None else []
        elif cut_created_at:
            targets = [e for e in entries if e.created_at >= cut_created_at]
        else:
            targets = []

        restored = 0
        deleted = 0
        skipped = 0
        paths: list[str] = []
        for e in reversed(targets):
            path = Path(e.path)
            try:
                if not e.existed:
                    if path.is_file():
                        path.unlink()
                        deleted += 1
                        paths.append(str(path))
                    else:
                        skipped += 1
                    continue
                if e.previous_content is None:
                    skipped += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(e.previous_content, encoding="utf-8")
                restored += 1
                paths.append(str(path))
            except OSError as exc:
                logger.warning("time-travel restore failed for %s: %s", path, exc)
                skipped += 1

        # Truncate log: drop entries we just undid
        keep = [e for e in entries if e not in targets]
        try:
            p = self._path(session_id)
            if keep:
                p.write_text(
                    "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in keep)
                    + "\n",
                    encoding="utf-8",
                )
            elif p.exists():
                p.unlink()
        except OSError:
            pass

        return {
            "restored": restored,
            "deleted": deleted,
            "skipped": skipped,
            "paths": paths[:50],
        }


def build_timeline(
    messages: list[Any],
    *,
    include_reverted: bool = False,
) -> list[dict[str, Any]]:
    """Build visual timeline steps from chat messages.

    Each user message starts a step; following assistant messages attach as
    outcomes until the next user turn. Tool process steps become sub-nodes.
    """
    steps: list[dict[str, Any]] = []
    step_n = 0
    current: dict[str, Any] | None = None

    for msg in messages:
        role = getattr(msg, "role", None)
        if role is None:
            role_val = ""
        elif hasattr(role, "value"):
            role_val = str(role.value)
        else:
            role_val = str(role)
        reverted = bool(getattr(msg, "reverted", False))
        if reverted and not include_reverted:
            continue
        mid = str(getattr(msg, "id", "") or "")
        content = getattr(msg, "content", "") or ""
        preview = (content if isinstance(content, str) else str(content)).strip()
        if len(preview) > 160:
            preview = preview[:157] + "…"
        created = getattr(msg, "created_at", None)
        if created is not None and hasattr(created, "isoformat"):
            created_s = str(created.isoformat())
        else:
            created_s = str(created or "")
        tools = list(getattr(msg, "tool_calls", None) or [])
        tool_names: list[str] = []
        for t in tools:
            if isinstance(t, dict):
                tool_names.append(str(t.get("name") or "?"))
            else:
                tool_names.append(str(getattr(t, "name", None) or "?"))

        if role_val == "user":
            step_n += 1
            current = {
                "step": step_n,
                "id": mid,
                "kind": "user",
                "label": f"Step {step_n}",
                "preview": preview or "(empty prompt)",
                "created_at": created_s,
                "message_id": mid,
                "tool_count": 0,
                "tools": [],
                "assistant_ids": [],
                "can_restore": True,
            }
            steps.append(current)
        elif role_val == "assistant" and current is not None:
            current["assistant_ids"].append(mid)
            current["tool_count"] = int(current.get("tool_count") or 0) + len(tool_names)
            current["tools"] = list(
                dict.fromkeys(list(current.get("tools") or []) + tool_names)
            )[:12]
            if preview and not current.get("assistant_preview"):
                current["assistant_preview"] = preview
            # Also allow rolling back *to before this assistant* by targeting
            # the parent user step (default). Expose assistant node for precision.
            steps.append(
                {
                    "step": step_n,
                    "id": mid,
                    "kind": "assistant",
                    "label": f"Step {step_n} · reply",
                    "preview": preview or "(assistant)",
                    "created_at": created_s,
                    "message_id": mid,
                    "parent_user_id": current["message_id"],
                    "tool_count": len(tool_names),
                    "tools": tool_names[:12],
                    "can_restore": True,
                }
            )
        elif role_val == "system":
            steps.append(
                {
                    "step": step_n or 0,
                    "id": mid,
                    "kind": "system",
                    "label": "System",
                    "preview": preview,
                    "created_at": created_s,
                    "message_id": mid,
                    "can_restore": False,
                }
            )

    return steps
