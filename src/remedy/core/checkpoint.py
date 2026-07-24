"""Mid-task checkpoints for long Build runs (personal partner Phase B2).

Snapshots progress so a stalled or soft-failed turn still leaves a clear
"what was done / what's next" trail — without multi-agent orchestration.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TurnCheckpoint:
    """One progress snapshot during a multi-step agent turn."""

    id: str
    session_id: str | None
    title: str
    done: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    tool_step_count: int = 0
    failures: list[str] = field(default_factory=list)
    plan_id: str | None = None
    reason: str = "manual"  # manual | auto | recovery | step_wall
    created_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TurnCheckpoint:
        return cls(
            id=str(raw.get("id") or uuid4().hex[:12]),
            session_id=raw.get("session_id"),
            title=str(raw.get("title") or "checkpoint"),
            done=[str(x) for x in (raw.get("done") or [])],
            next_steps=[str(x) for x in (raw.get("next_steps") or [])],
            tools_used=[str(x) for x in (raw.get("tools_used") or [])],
            tool_step_count=int(raw.get("tool_step_count") or 0),
            failures=[str(x) for x in (raw.get("failures") or [])],
            plan_id=raw.get("plan_id"),
            reason=str(raw.get("reason") or "manual"),
            created_at=str(raw.get("created_at") or _now()),
            metadata=dict(raw.get("metadata") or {}),
        )

    def summary_markdown(self) -> str:
        lines = [
            f"# Checkpoint: {self.title}",
            f"**When:** {self.created_at}",
            f"**Reason:** {self.reason}",
            f"**Tools so far:** {self.tool_step_count}",
        ]
        if self.done:
            lines.append("")
            lines.append("## Done")
            for d in self.done:
                lines.append(f"- {d}")
        if self.next_steps:
            lines.append("")
            lines.append("## Next")
            for n in self.next_steps:
                lines.append(f"- {n}")
        if self.failures:
            lines.append("")
            lines.append("## Failures / blockers")
            for f in self.failures:
                lines.append(f"- {f}")
        if self.tools_used:
            lines.append("")
            lines.append(f"_Tools: {', '.join(self.tools_used[:20])}_")
        return "\n".join(lines)


class CheckpointStore:
    """Filesystem store under ``{home}/checkpoints``."""

    def __init__(self, home_dir: Path | str | None = None) -> None:
        home = Path(home_dir).expanduser() if home_dir else Path.home() / ".remedy"
        self.home = home
        self.root = home / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, cp_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", cp_id) or "cp"
        return self.root / f"{safe}.json"

    def save(self, cp: TurnCheckpoint) -> TurnCheckpoint:
        path = self._path(cp.id)
        path.write_text(json.dumps(cp.to_dict(), indent=2), encoding="utf-8")
        return cp

    def get(self, cp_id: str) -> TurnCheckpoint | None:
        path = self._path(cp_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return TurnCheckpoint.from_dict(raw)

    def list_for_session(
        self, session_id: str | None, *, limit: int = 20
    ) -> list[TurnCheckpoint]:
        items: list[TurnCheckpoint] = []
        for path in sorted(
            self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            cp = TurnCheckpoint.from_dict(raw)
            if session_id and cp.session_id and cp.session_id != session_id:
                continue
            items.append(cp)
            if len(items) >= limit:
                break
        return items

    def latest(self, session_id: str | None = None) -> TurnCheckpoint | None:
        items = self.list_for_session(session_id, limit=1)
        return items[0] if items else None


def build_checkpoint_from_tool_steps(
    steps: list[dict[str, Any]] | None,
    *,
    session_id: str | None = None,
    title: str = "",
    reason: str = "auto",
    plan_id: str | None = None,
) -> TurnCheckpoint:
    """Derive a checkpoint from agent ``_turn_tool_steps`` dicts."""
    steps = list(steps or [])
    done: list[str] = []
    failures: list[str] = []
    tools: list[str] = []
    for s in steps:
        name = str(s.get("tool") or s.get("tool_name") or "tool")
        tools.append(name)
        ok = bool(s.get("success", True))
        summary = str(s.get("result") or s.get("result_summary") or "")[:120]
        if ok:
            done.append(f"{name}: {summary}" if summary else name)
        else:
            err = str(s.get("error") or "failed")[:120]
            failures.append(f"{name}: {err}")
    # Deduplicate tools preserving order
    seen: set[str] = set()
    unique_tools: list[str] = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            unique_tools.append(t)
    next_steps: list[str] = []
    if failures:
        next_steps.append("Retry or work around the last failing tool(s)")
    if done and not failures:
        next_steps.append("Continue remaining work from the last successful tool")
    if not next_steps:
        next_steps.append("Resume the user request with remaining steps")
    return TurnCheckpoint(
        id=uuid4().hex[:12],
        session_id=session_id,
        title=(title or "Mid-task checkpoint")[:200],
        done=done[-12:],
        next_steps=next_steps[:8],
        tools_used=unique_tools[:30],
        tool_step_count=len(steps),
        failures=failures[-8:],
        plan_id=plan_id,
        reason=reason,
    )


# How often to auto-snapshot during a long ReAct loop (tool batch count).
AUTO_CHECKPOINT_EVERY_N_STEPS = 4
