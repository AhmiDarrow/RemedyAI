"""Skill refiner -- improves existing skills based on execution feedback.

Monitors skill execution success/failure signals and:
- Proposes improvements to instructions
- Adjusts confidence scores
- Tracks version history with changelogs
- Auto-promotes/demotes/deprecates based on performance
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from packaging.version import Version

from remedy.models import Skill, SkillStatus


@dataclass
class RefinementRecord:
    """A single refinement applied to a skill."""
    id: UUID = field(default_factory=uuid4)
    skill_name: str = ""
    from_version: str = ""
    to_version: str = ""
    change_type: str = ""  # "instruction", "tag", "tool", "confidence", "status"
    change_description: str = ""
    triggered_by: str = ""  # "feedback", "manual", "auto-analysis"
    feedback_context: str | None = None
    applied_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SkillStats:
    """Aggregated execution statistics for a skill."""
    skill_name: str
    total_executions: int = 0
    successes: int = 0
    failures: int = 0
    avg_duration_ms: float = 0.0
    last_executed: datetime | None = None
    execution_by_session: dict[str, int] = field(default_factory=dict)
    common_errors: dict[str, int] = field(default_factory=dict)
    consecutive_failures: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_executions == 0:
            return 0.5
        return self.successes / self.total_executions

    @property
    def is_reliable(self) -> bool:
        # Align with lifecycle: volume + rate + multi-session (not one lucky streak)
        sessions = len(self.execution_by_session)
        return (
            self.total_executions >= 5
            and self.success_rate >= 0.8
            and sessions >= 2
        )

    @property
    def is_unreliable(self) -> bool:
        return self.total_executions >= 5 and self.success_rate < 0.5


class SkillRefiner:
    """Refines skills based on execution feedback and statistics.

    Stats can be persisted to JSON so promote/demote survives restarts.
    """

    def __init__(self, stats_path: Path | str | None = None) -> None:
        self._stats: dict[str, SkillStats] = {}
        self._history: list[RefinementRecord] = []
        # Track consecutive failure streaks per skill
        self._failure_streak: dict[str, int] = {}
        self._last_success_at: dict[str, datetime] = {}
        self._last_failure_at: dict[str, datetime] = {}
        self._stats_path: Path | None = (
            Path(stats_path).expanduser() if stats_path else None
        )
        if self._stats_path is not None:
            self.load_stats(self._stats_path)

    def set_stats_path(self, path: Path | str | None) -> None:
        self._stats_path = Path(path).expanduser() if path else None
        if self._stats_path is not None and self._stats_path.is_file():
            self.load_stats(self._stats_path)

    def load_stats(self, path: Path | str | None = None) -> int:
        """Load durable stats from JSON. Returns number of skills loaded."""
        p = Path(path).expanduser() if path else self._stats_path
        if p is None or not p.is_file():
            return 0
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        skills = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(skills, dict):
            return 0
        n = 0
        for name, raw in skills.items():
            if not isinstance(raw, dict):
                continue
            stats = SkillStats(
                skill_name=str(name),
                total_executions=int(raw.get("total_executions") or 0),
                successes=int(raw.get("successes") or 0),
                failures=int(raw.get("failures") or 0),
                avg_duration_ms=float(raw.get("avg_duration_ms") or 0.0),
                execution_by_session={
                    str(k): int(v)
                    for k, v in (raw.get("execution_by_session") or {}).items()
                },
                common_errors={
                    str(k): int(v) for k, v in (raw.get("common_errors") or {}).items()
                },
                consecutive_failures=int(raw.get("consecutive_failures") or 0),
            )
            le = raw.get("last_executed")
            if le:
                try:
                    stats.last_executed = datetime.fromisoformat(str(le))
                except ValueError:
                    pass
            self._stats[str(name)] = stats
            self._failure_streak[str(name)] = stats.consecutive_failures
            ls = raw.get("last_success_at")
            lf = raw.get("last_failure_at")
            if ls:
                try:
                    self._last_success_at[str(name)] = datetime.fromisoformat(str(ls))
                except ValueError:
                    pass
            if lf:
                try:
                    self._last_failure_at[str(name)] = datetime.fromisoformat(str(lf))
                except ValueError:
                    pass
            n += 1
        return n

    def save_stats(self, path: Path | str | None = None) -> bool:
        """Persist stats to JSON. Returns True on success."""
        p = Path(path).expanduser() if path else self._stats_path
        if p is None:
            return False
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {"version": 1, "skills": {}}
            for name, stats in self._stats.items():
                payload["skills"][name] = {
                    "total_executions": stats.total_executions,
                    "successes": stats.successes,
                    "failures": stats.failures,
                    "avg_duration_ms": stats.avg_duration_ms,
                    "last_executed": (
                        stats.last_executed.isoformat() if stats.last_executed else None
                    ),
                    "execution_by_session": dict(stats.execution_by_session),
                    "common_errors": dict(stats.common_errors),
                    "consecutive_failures": stats.consecutive_failures,
                    "last_success_at": (
                        self._last_success_at[name].isoformat()
                        if name in self._last_success_at
                        else None
                    ),
                    "last_failure_at": (
                        self._last_failure_at[name].isoformat()
                        if name in self._last_failure_at
                        else None
                    ),
                }
            p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return True
        except OSError:
            return False

    def record_execution(
        self,
        skill_name: str,
        success: bool,
        duration_ms: float = 0.0,
        session_id: str = "",
        error: str | None = None,
    ) -> None:
        stats = self._get_or_create_stats(skill_name)
        stats.total_executions += 1
        now = datetime.now(UTC)
        if success:
            stats.successes += 1
            self._failure_streak[skill_name] = 0
            self._last_success_at[skill_name] = now
        else:
            stats.failures += 1
            self._failure_streak[skill_name] = self._failure_streak.get(skill_name, 0) + 1
            self._last_failure_at[skill_name] = now

        stats.consecutive_failures = self._failure_streak.get(skill_name, 0)

        if stats.total_executions == 1:
            stats.avg_duration_ms = duration_ms
        else:
            stats.avg_duration_ms = (
                stats.avg_duration_ms * (stats.total_executions - 1) + duration_ms
            ) / stats.total_executions

        stats.last_executed = now
        if session_id:
            stats.execution_by_session[session_id] = stats.execution_by_session.get(session_id, 0) + 1

        if error and not success:
            error_key = error[:80]
            stats.common_errors[error_key] = stats.common_errors.get(error_key, 0) + 1

        # Durable write (best-effort)
        self.save_stats()

    def get_stats(self, skill_name: str) -> SkillStats:
        return self._get_or_create_stats(skill_name)

    def get_all_stats(self) -> dict[str, SkillStats]:
        return dict(self._stats)

    def should_promote(self, skill_name: str) -> bool:
        stats = self._get_or_create_stats(skill_name)
        return stats.is_reliable and self._failure_streak.get(skill_name, 0) == 0

    def should_demote(self, skill_name: str) -> bool:
        stats = self._get_or_create_stats(skill_name)
        if self._failure_streak.get(skill_name, 0) >= 3:
            return True
        return stats.is_unreliable

    def failure_streak(self, skill_name: str) -> int:
        return self._failure_streak.get(skill_name, 0)

    def session_count(self, skill_name: str) -> int:
        return len(self._get_or_create_stats(skill_name).execution_by_session)

    def last_success_at(self, skill_name: str) -> datetime | None:
        return self._last_success_at.get(skill_name)

    def last_failure_at(self, skill_name: str) -> datetime | None:
        return self._last_failure_at.get(skill_name)

    def refine_instructions(
        self,
        skill: Skill,
        suggestion: str,
    ) -> RefinementRecord | None:
        """Add or update a section of the skill's instructions."""
        old_instructions = skill.instructions
        old_version = skill.manifest.version

        new_instructions = old_instructions.strip() + "\n\n## Refinement\n\n" + suggestion
        skill.instructions = new_instructions

        try:
            v = Version(old_version)
            new_version = f"{v.major}.{v.minor}.{v.micro + 1}"
        except Exception:
            new_version = old_version

        skill.manifest.version = new_version

        record = RefinementRecord(
            skill_name=skill.manifest.name,
            from_version=old_version,
            to_version=new_version,
            change_type="instruction",
            change_description=f"Added refinement: {suggestion[:100]}",
            triggered_by="auto-analysis",
        )
        self._history.append(record)
        return record

    def adjust_confidence(
        self,
        skill: Skill,
        skill_name: str,
    ) -> RefinementRecord | None:
        """Adjust skill status based on execution feedback."""
        stats = self._get_or_create_stats(skill_name)
        old_status = skill.manifest.status

        if stats.is_reliable and skill.manifest.status != SkillStatus.ACTIVE:
            skill.manifest.status = SkillStatus.ACTIVE
        elif stats.is_unreliable and skill.manifest.status == SkillStatus.ACTIVE:
            skill.manifest.status = SkillStatus.DISABLED

        if skill.manifest.status != old_status:
            record = RefinementRecord(
                skill_name=skill_name,
                from_version=skill.manifest.version,
                to_version=skill.manifest.version,
                change_type="status",
                change_description=f"Status changed from {old_status.value} to {skill.manifest.status.value} "
                                   f"(success_rate={stats.success_rate:.0%}, n={stats.total_executions})",
                triggered_by="feedback",
            )
            self._history.append(record)
            return record
        return None

    def suggest_fixes(
        self,
        skill_name: str,
    ) -> list[str]:
        """Analyze common errors and suggest instruction fixes."""
        stats = self._get_or_create_stats(skill_name)
        suggestions: list[str] = []

        for error, count in stats.common_errors.items():
            if count >= 2:
                suggestions.append(
                    f"Skill '{skill_name}' failed {count} times with error: '{error}'. "
                    f"Consider adding error handling for this case."
                )

        if stats.success_rate < 0.5 and stats.total_executions >= 3:
            suggestions.append(
                f"Skill '{skill_name}' has low success rate ({stats.success_rate:.0%}). "
                f"Review the instructions for correctness."
            )

        return suggestions

    def generate_changelog(self, skill_name: str | None = None) -> str:
        history = self._history
        if skill_name:
            history = [r for r in history if r.skill_name == skill_name]
        if not history:
            if skill_name:
                return f"No refinements recorded for skill '{skill_name}'."
            return "No refinements recorded."
        lines = ["# Skill Refinement Changelog", ""]
        for r in sorted(history, key=lambda x: x.applied_at, reverse=True):
            lines.append(
                f"- **{r.skill_name}** {r.from_version} -> {r.to_version} "
                f"({r.change_type}): {r.change_description}"
            )
        return "\n".join(lines)

    @property
    def refinement_count(self) -> int:
        return len(self._history)

    def _get_or_create_stats(self, skill_name: str) -> SkillStats:
        if skill_name not in self._stats:
            self._stats[skill_name] = SkillStats(skill_name=skill_name)
        return self._stats[skill_name]
