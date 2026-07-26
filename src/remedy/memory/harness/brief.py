"""Session Brief — L2 anchored structured state for Memory Harness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class DecisionRecord(BaseModel):
    """Decision with reasoning — survives multi-cycle compaction."""

    decision: str = ""
    why: str = ""
    rejected: str = ""

    def format_line(self) -> str:
        parts = [self.decision.strip()]
        if self.why.strip():
            parts.append(f"Why: {self.why.strip()}")
        if self.rejected.strip():
            parts.append(f"Rejected: {self.rejected.strip()}")
        return " — ".join(p for p in parts if p)


class HistoryThreadEntry(BaseModel):
    """One compaction epoch — cumulative, never overwritten."""

    n: int = 0
    summary: str = ""
    decisions_why: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    at: str = ""


class SessionBrief(BaseModel):
    """Persistent mid-session working memory (injected every turn)."""

    session_id: str = ""
    intent: str = ""
    decisions: list[str] = Field(default_factory=list)
    # Structured decisions (preferred); plain decisions kept for compat
    decision_records: list[DecisionRecord] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    open_tasks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    key_paths: list[str] = Field(default_factory=list)
    user_constraints: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    notes: str = ""
    # Cumulative multi-compaction arc (Compaction Memory pattern)
    history_thread: list[HistoryThreadEntry] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    compress_count: int = 0
    last_quality_score: float | None = None

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

    def add_decision_record(
        self,
        decision: str,
        *,
        why: str = "",
        rejected: str = "",
        limit: int = 20,
    ) -> None:
        d = (decision or "").strip()
        if not d:
            return
        # Dedupe by decision text
        for rec in self.decision_records:
            if rec.decision.lower() == d.lower():
                if why and not rec.why:
                    rec.why = why.strip()[:400]
                if rejected and not rec.rejected:
                    rec.rejected = rejected.strip()[:400]
                self.touch()
                return
        self.decision_records.append(
            DecisionRecord(
                decision=d[:400],
                why=(why or "").strip()[:400],
                rejected=(rejected or "").strip()[:400],
            )
        )
        self.decision_records = self.decision_records[-limit:]
        # Mirror into plain decisions for older consumers
        if d not in self.decisions:
            self.decisions.append(d)
            self.decisions = self.decisions[-limit:]
        self.touch()

    def append_history_thread(
        self,
        summary: str,
        *,
        decisions_why: list[str] | None = None,
        blockers: list[str] | None = None,
        limit: int = 12,
    ) -> None:
        """Append a cumulative compaction entry (never overwrite prior)."""
        text = (summary or "").strip()
        if not text:
            return
        n = len(self.history_thread) + 1
        self.history_thread.append(
            HistoryThreadEntry(
                n=n,
                summary=text[:600],
                decisions_why=[str(x).strip()[:200] for x in (decisions_why or []) if str(x).strip()][
                    :6
                ],
                blockers=[str(x).strip()[:200] for x in (blockers or []) if str(x).strip()][:4],
                at=datetime.now(UTC).isoformat(),
            )
        )
        # Soft-trim oldest entries if too many (keep newest)
        if len(self.history_thread) > limit:
            self.history_thread = self.history_thread[-limit:]
            # Renumber for display
            for i, ent in enumerate(self.history_thread, 1):
                ent.n = i
        self.touch()

    def merge_summary(
        self,
        *,
        intent: str | None = None,
        decisions: list[str] | None = None,
        decision_records: list[dict[str, Any] | DecisionRecord] | None = None,
        open_tasks: list[str] | None = None,
        next_steps: list[str] | None = None,
        blockers: list[str] | None = None,
        notes: str | None = None,
        history_summary: str | None = None,
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
        if decision_records:
            for raw in decision_records:
                if isinstance(raw, DecisionRecord):
                    self.add_decision_record(
                        raw.decision, why=raw.why, rejected=raw.rejected
                    )
                elif isinstance(raw, dict):
                    self.add_decision_record(
                        str(raw.get("decision") or raw.get("text") or ""),
                        why=str(raw.get("why") or ""),
                        rejected=str(raw.get("rejected") or ""),
                    )
        if open_tasks is not None:
            self.open_tasks = [t.strip() for t in open_tasks if (t or "").strip()][:20]
        if next_steps is not None:
            self.next_steps = [t.strip() for t in next_steps if (t or "").strip()][:15]
        if blockers is not None:
            self.blockers = [b.strip() for b in blockers if (b or "").strip()][:10]
        if notes is not None and notes.strip():
            self.notes = notes.strip()[:2000]
        if history_summary and history_summary.strip():
            self.append_history_thread(
                history_summary,
                decisions_why=[
                    r.format_line()
                    for r in self.decision_records[-3:]
                ]
                or list(self.decisions[-3:]),
                blockers=list(self.blockers[-3:]),
            )
        self.compress_count += 1
        self.touch()


def brief_to_context_block(brief: SessionBrief | None, *, max_chars: int = 2200) -> str:
    """Markdown block for system context; empty if brief has nothing useful."""
    if brief is None:
        return ""
    lines: list[str] = [
        "Session Brief (working memory — trust this over stale chat; re-read files if detail needed):"
    ]
    if brief.intent:
        lines.append(f"- Intent: {brief.intent}")
    # Prefer structured decisions
    recs = list(brief.decision_records or [])
    if recs:
        lines.append("- Decisions (do not re-litigate):")
        for r in recs[-8:]:
            lines.append(f"  · {r.format_line()}")
    elif brief.decisions:
        lines.append("- Decisions:")
        for d in brief.decisions[-8:]:
            lines.append(f"  · {d}")
    if brief.history_thread:
        lines.append("- Historical context (cumulative):")
        for ent in brief.history_thread[-6:]:
            lines.append(f"  · [C{ent.n}] {ent.summary}")
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
    if brief.last_quality_score is not None:
        lines.append(f"- Continuity quality: {brief.last_quality_score:.0%}")
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
