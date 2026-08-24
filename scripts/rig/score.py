"""Scoring and reporting for a harness run.

A model is judged on two axes: whether it cleared each rung (weighted pass
rate) and how it behaved getting there (tool calls spent, failures, latency).
The second axis is what separates a model that technically passes from one you
would actually leave running.
"""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import Turn
from .scenarios import Scenario


@dataclass
class Outcome:
    id: str
    tier: int
    weight: int
    passed: bool
    detail: str
    seconds: float
    tool_calls: int
    failed_tools: int
    first_tool_s: float | None
    status: str
    error: str
    text_chars: int
    usage: dict[str, Any] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)
    # Why a tool call failed - without this a scorecard says "1 failed"
    # and leaves you re-running the whole suite to find out what.
    tool_failures: list[dict[str, Any]] = field(default_factory=list)


def grade(scenario: Scenario, turn: Turn, workspace: Path) -> Outcome:
    """Run the scenario's check and fold in the behavioural metrics."""
    if turn.status == "error" or turn.error:
        passed, detail = False, f"stream error: {turn.error[:200]}"
    else:
        try:
            passed, detail = scenario.check(turn, workspace)
        except Exception as e:  # a check that explodes is a failed rung
            passed, detail = False, f"check raised {type(e).__name__}: {e}"
    return Outcome(
        id=scenario.id,
        tier=scenario.tier,
        weight=scenario.weight,
        passed=passed,
        detail=detail,
        seconds=round(turn.seconds, 1),
        tool_calls=len(turn.tool_calls),
        failed_tools=len(turn.failed_tools),
        first_tool_s=(round(turn.first_tool_s, 1) if turn.first_tool_s else None),
        status=turn.status,
        error=turn.error[:400],
        text_chars=len(turn.text),
        usage=turn.usage or {},
        tool_names=turn.tool_names,
        tool_failures=[
            {"name": c.name, "args": c.args, "preview": c.preview[:400]}
            for c in turn.failed_tools
        ],
    )


@dataclass
class RunReport:
    label: str
    provider: str
    model: str
    base_url: str
    suite: str
    outcomes: list[Outcome] = field(default_factory=list)
    started: str = ""
    seconds: float = 0.0
    host: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # -- aggregates ------------------------------------------------------

    @property
    def earned(self) -> int:
        return sum(o.weight for o in self.outcomes if o.passed)

    @property
    def possible(self) -> int:
        return sum(o.weight for o in self.outcomes)

    @property
    def pct(self) -> float:
        return (100.0 * self.earned / self.possible) if self.possible else 0.0

    @property
    def top_tier(self) -> int:
        """Highest rung cleared with every rung below it also cleared."""
        by_tier: dict[int, list[Outcome]] = {}
        for o in self.outcomes:
            by_tier.setdefault(o.tier, []).append(o)
        top = -1
        for tier in sorted(by_tier):
            if all(o.passed for o in by_tier[tier]):
                top = tier
            else:
                break
        return top

    @property
    def total_tool_calls(self) -> int:
        return sum(o.tool_calls for o in self.outcomes)

    @property
    def total_failed_tools(self) -> int:
        return sum(o.failed_tools for o in self.outcomes)

    @property
    def verdict(self) -> str:
        """One-line judgement on whether this model can run Remedy."""
        t = self.top_tier
        if t >= 8:
            return "RUNS REMEDY - sustained multi-step work, safe to leave running"
        if t >= 6:
            return "RUNS REMEDY - handles real multi-file tasks"
        if t >= 5:
            return "WORKABLE - edits, runs, and recovers from errors"
        if t >= 3:
            return "MARGINAL - writes and edits, but brittle past a single step"
        if t >= 1:
            return "TOY - emits tool calls, cannot sustain a task"
        return "CANNOT DRIVE REMEDY"

    # -- output ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "suite": self.suite,
            "started": self.started,
            "seconds": round(self.seconds, 1),
            "score": {
                "earned": self.earned,
                "possible": self.possible,
                "pct": round(self.pct, 1),
                "top_tier": self.top_tier,
                "verdict": self.verdict,
            },
            "totals": {
                "tool_calls": self.total_tool_calls,
                "failed_tools": self.total_failed_tools,
            },
            "host": self.host,
            "notes": self.notes,
            "outcomes": [asdict(o) for o in self.outcomes],
        }

    def write(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.label}.json"
        path.write_text(
            json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        return path

    def render(self) -> str:
        w = max([len(o.id) for o in self.outcomes] + [8])
        lines = [
            "",
            f"  {self.label}  ({self.provider}/{self.model or '-'})",
            f"  {'-' * (w + 54)}",
            f"  {'scenario'.ljust(w)}  t  {'result':7} {'tools':>5} {'fail':>4} {'secs':>6}  detail",
        ]
        for o in sorted(self.outcomes, key=lambda x: x.tier):
            mark = "PASS" if o.passed else "FAIL"
            lines.append(
                f"  {o.id.ljust(w)}  {o.tier}  {mark:7} {o.tool_calls:>5} "
                f"{o.failed_tools:>4} {o.seconds:>6.0f}  {o.detail[:70]}"
            )
        lines += [
            f"  {'-' * (w + 54)}",
            f"  score {self.earned}/{self.possible} ({self.pct:.0f}%)   "
            f"highest clean tier: {self.top_tier}   "
            f"tools {self.total_tool_calls} ({self.total_failed_tools} failed)",
            f"  VERDICT: {self.verdict}",
            "",
        ]
        return "\n".join(lines)


def host_info() -> dict[str, Any]:
    """Snapshot of the test-bed so a scorecard is reproducible."""
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor(),
    }
    try:
        import subprocess

        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            info["gpu"] = out.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return info


def now_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def compare(reports: list[RunReport]) -> str:
    """Side-by-side table across models."""
    if not reports:
        return "no runs"
    ids = sorted({o.id for r in reports for o in r.outcomes}, key=lambda i: next(
        o.tier for r in reports for o in r.outcomes if o.id == i
    ))
    w = max([len(i) for i in ids] + [8])
    cols = [r.label[:18] for r in reports]
    head = f"  {'scenario'.ljust(w)}  " + "  ".join(c.rjust(18) for c in cols)
    lines = ["", head, f"  {'-' * len(head)}"]
    for sid in ids:
        row = [sid.ljust(w)]
        for r in reports:
            o = next((x for x in r.outcomes if x.id == sid), None)
            row.append(("-" if o is None else ("PASS" if o.passed else "FAIL")).rjust(18))
        lines.append("  " + "  ".join(row))
    lines.append(f"  {'-' * len(head)}")
    lines.append(
        "  " + "score".ljust(w) + "  "
        + "  ".join(f"{r.pct:.0f}% (t{r.top_tier})".rjust(18) for r in reports)
    )
    lines.append("")
    for r in reports:
        lines.append(f"  {r.label}: {r.verdict}")
    lines.append("")
    return "\n".join(lines)
