"""Skill refiner -- improves existing skills based on execution feedback.

Monitors skill execution success/failure signals and:
- Proposes improvements to instructions
- Adjusts confidence scores
- Tracks version history with changelogs
- Auto-promotes/demotes/deprecates based on performance
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID, uuid4

from packaging.version import Version

from remedy.models import Skill, SkillManifest, SkillStatus


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
    feedback_context: Optional[str] = None
    applied_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SkillStats:
    """Aggregated execution statistics for a skill."""
    skill_name: str
    total_executions: int = 0
    successes: int = 0
    failures: int = 0
    avg_duration_ms: float = 0.0
    last_executed: Optional[datetime] = None
    execution_by_session: dict[str, int] = field(default_factory=dict)
    common_errors: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.total_executions == 0:
            return 0.5
        return self.successes / self.total_executions

    @property
    def is_reliable(self) -> bool:
        return self.total_executions >= 3 and self.success_rate >= 0.8

    @property
    def is_unreliable(self) -> bool:
        return self.total_executions >= 5 and self.success_rate < 0.5


class SkillRefiner:
    """Refines skills based on execution feedback and statistics."""

    def __init__(self) -> None:
        self._stats: dict[str, SkillStats] = {}
        self._history: list[RefinementRecord] = []

    def record_execution(
        self,
        skill_name: str,
        success: bool,
        duration_ms: float = 0.0,
        session_id: str = "",
        error: Optional[str] = None,
    ) -> None:
        stats = self._get_or_create_stats(skill_name)
        stats.total_executions += 1
        if success:
            stats.successes += 1
        else:
            stats.failures += 1

        if stats.total_executions == 1:
            stats.avg_duration_ms = duration_ms
        else:
            stats.avg_duration_ms = (
                stats.avg_duration_ms * (stats.total_executions - 1) + duration_ms
            ) / stats.total_executions

        stats.last_executed = datetime.now(timezone.utc)
        if session_id:
            stats.execution_by_session[session_id] = stats.execution_by_session.get(session_id, 0) + 1

        if error and not success:
            error_key = error[:80]
            stats.common_errors[error_key] = stats.common_errors.get(error_key, 0) + 1

    def get_stats(self, skill_name: str) -> SkillStats:
        return self._get_or_create_stats(skill_name)

    def get_all_stats(self) -> dict[str, SkillStats]:
        return dict(self._stats)

    def should_promote(self, skill_name: str) -> bool:
        stats = self._get_or_create_stats(skill_name)
        return stats.is_reliable

    def should_demote(self, skill_name: str) -> bool:
        stats = self._get_or_create_stats(skill_name)
        return stats.is_unreliable

    def refine_instructions(
        self,
        skill: Skill,
        suggestion: str,
    ) -> Optional[RefinementRecord]:
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
    ) -> Optional[RefinementRecord]:
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

    def generate_changelog(self) -> str:
        if not self._history:
            return "No refinements recorded."
        lines = ["# Skill Refinement Changelog", ""]
        for r in sorted(self._history, key=lambda x: x.applied_at, reverse=True):
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
