"""Session Brief — L2 anchored structured state for Memory Harness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class SessionBrief(BaseModel):
    """Persistent mid-session working memory (injected every turn)."""

    session_id: str = ""
    intent: str = ""
    decisions: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    open_tasks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    key_paths: list[str] = Field(default_factory=list)
    user_constraints: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    notes: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    compress_count: int = 0

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def add_artifact(self, path: str, *, limit: int = 40) -> None:
        path = (path or "").strip()
        if not path:
            return
        if path not in self.artifacts:
            self.artifacts.append(path)
            if len(self.artifacts) > limit:
                self.artifacts = self.artifacts[-limit:]
        if path not in self.key_paths:
            self.key_paths.append(path)
            if len(self.key_paths) > limit:
                self.key_paths = self.key_paths[-limit:]
        self.touch()

    def merge_summary(
        self,
        *,
        intent: str | None = None,
        decisions: list[str] | None = None,
        open_tasks: list[str] | None = None,
        next_steps: list[str] | None = None,
        blockers: list[str] | None = None,
        notes: str | None = None,
    ) -> None:
        """Merge newly compressed span into the brief (anchored iterative update)."""
        if intent and intent.strip():
            self.intent = intent.strip()
        if decisions:
            for d in decisions:
                d = (d or "").strip()
                if d and d not in self.decisions:
                    self.decisions.append(d)
            self.decisions = self.decisions[-20:]
        if open_tasks is not None:
            self.open_tasks = [t.strip() for t in open_tasks if (t or "").strip()][:20]
        if next_steps is not None:
            self.next_steps = [t.strip() for t in next_steps if (t or "").strip()][:15]
        if blockers is not None:
            self.blockers = [b.strip() for b in blockers if (b or "").strip()][:10]
        if notes is not None and notes.strip():
            self.notes = notes.strip()[:2000]
        self.compress_count += 1
        self.touch()


def brief_to_context_block(brief: SessionBrief | None, *, max_chars: int = 1800) -> str:
    """Markdown block for system context; empty if brief has nothing useful."""
    if brief is None:
        return ""
    lines: list[str] = ["Session Brief (working memory — trust this over stale chat):"]
    if brief.intent:
        lines.append(f"- Intent: {brief.intent}")
    if brief.decisions:
        lines.append("- Decisions:")
        for d in brief.decisions[-8:]:
            lines.append(f"  · {d}")
    if brief.artifacts:
        lines.append("- Artifacts / files touched:")
        for a in brief.artifacts[-12:]:
            lines.append(f"  · {a}")
    if brief.open_tasks:
        lines.append("- Open tasks:")
        for t in brief.open_tasks[-8:]:
            lines.append(f"  · {t}")
    if brief.blockers:
        lines.append("- Blockers:")
        for b in brief.blockers[-5:]:
            lines.append(f"  · {b}")
    if brief.user_constraints:
        lines.append("- Constraints:")
        for c in brief.user_constraints[-6:]:
            lines.append(f"  · {c}")
    if brief.next_steps:
        lines.append("- Next steps:")
        for s in brief.next_steps[-6:]:
            lines.append(f"  · {s}")
    if brief.notes:
        lines.append(f"- Notes: {brief.notes[:400]}")
    if len(lines) <= 1:
        return ""
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def brief_from_dict(data: dict[str, Any] | None) -> SessionBrief | None:
    if not data:
        return None
    try:
        return SessionBrief.model_validate(data)
    except Exception:
        return None
