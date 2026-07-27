"""Types for silent spread (fan-out) workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Deterministic worker kinds (prefer these — cheap and parallelizable).
SPREAD_KINDS = frozenset(
    {"explore", "search", "verify", "diff", "read_map", "review"}
)


@dataclass
class SpreadTask:
    id: str
    kind: str  # explore | search | verify | diff | read_map | review
    goal: str = ""
    path: str = "."
    query: str = ""
    command: str = ""
    max_steps: int = 4
    max_chars: int = 6_000

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "goal": self.goal,
            "path": self.path,
            "query": self.query,
            "command": self.command,
            "max_steps": self.max_steps,
            "max_chars": self.max_chars,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, index: int = 0) -> SpreadTask:
        kind = str(data.get("kind") or "explore").strip().lower()
        if kind not in SPREAD_KINDS:
            kind = "explore"
        tid = str(data.get("id") or f"t{index + 1}").strip() or f"t{index + 1}"
        return cls(
            id=tid,
            kind=kind,
            goal=str(data.get("goal") or data.get("query") or "")[:500],
            path=str(data.get("path") or ".")[:800],
            query=str(data.get("query") or data.get("goal") or "")[:400],
            command=str(data.get("command") or "")[:800],
            max_steps=max(1, min(12, int(data.get("max_steps") or 4))),
            max_chars=max(500, min(14_000, int(data.get("max_chars") or 6_000))),
        )


@dataclass
class WorkerResult:
    id: str
    kind: str
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    model_used: str = "none"  # none | local | frontier

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "ok": self.ok,
            "summary": self.summary[:2000],
            "elapsed_ms": round(self.elapsed_ms, 1),
            "model_used": self.model_used,
        }


@dataclass
class SpreadResult:
    ok: bool
    strategy: str  # fanout | single | skipped
    reason: str
    tasks: list[SpreadTask] = field(default_factory=list)
    results: list[WorkerResult] = field(default_factory=list)
    merged_summary: str = ""
    wall_ms: float = 0.0
    max_workers: int = 4

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strategy": self.strategy,
            "reason": self.reason,
            "task_count": len(self.tasks),
            "result_count": len(self.results),
            "wall_ms": round(self.wall_ms, 1),
            "max_workers": self.max_workers,
            "ok_count": sum(1 for r in self.results if r.ok),
        }
