"""Learning loop -- the self-improvement orchestrator.

Orchestrates reflection, skill generation, refinement, and procedural
memory integration. The single entry point for Remedy's "grows with you"
philosophy, inspired by Hermes' autonomous learning.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from remedy.core.learning.procedural import LearningEvent, LearningHistory
from remedy.core.learning.refiner import SkillRefiner
from remedy.core.learning.reflection import (
    ExecutionTrace,
    ReflectionEngine,
    TraceStep,
)
from remedy.memory.store import MemoryStore
from remedy.models import (
    MemoryEntryType,
    Skill,
    SkillKind,
    SkillManifest,
    SkillStatus,
    Task,
    TaskStatus,
)


class LearningLoop:
    """Orchestrates the full learning cycle.

    Usage:
        loop = LearningLoop(skills_dir=Path("~/.remedy/skills"), memory=store)

        # After a complex task:
        trace = ExecutionTrace(task_id=task.id, title=task.title, steps=[...])
        result = loop.learn_from_trace(trace)

        # After skill execution feedback:
        loop.record_skill_feedback("my-skill", success=True, duration_ms=150)
    """

    def __init__(
        self,
        skills_dir: Path,
        memory: MemoryStore,
        auto_approve_threshold: float = 0.8,
    ) -> None:
        self.skills_dir = Path(skills_dir).expanduser().resolve()
        self.memory = memory
        self.auto_approve_threshold = auto_approve_threshold
        self.reflection = ReflectionEngine()
        self.refiner = SkillRefiner()
        self.history = LearningHistory()
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    # -- public API: learning from traces ------------------------------------

    def build_trace_from_dict(
        self,
        task: Task,
        steps: list[dict],
        session_id: str | None = None,
    ) -> ExecutionTrace:
        """Build an ExecutionTrace from a task and raw step dicts."""
        trace_steps = []
        for i, s in enumerate(steps):
            trace_steps.append(TraceStep(
                index=i,
                tool_name=s.get("tool", s.get("tool_name", f"step_{i}")),
                arguments=s.get("arguments", s.get("args", {})),
                result_summary=str(s.get("result", s.get("summary", "")))[:200],
                success=s.get("success", True),
                duration_ms=s.get("duration_ms", 0.0),
                error=s.get("error"),
            ))

        return ExecutionTrace(
            task_id=task.id,
            title=task.title,
            description=task.description,
            steps=trace_steps,
            overall_success=task.status == TaskStatus.COMPLETED,
            tags=task.tags,
            session_id=session_id,
        )

    def learn_from_trace(
        self,
        trace: ExecutionTrace,
        auto_approve: bool | None = None,
    ) -> Skill | None:
        """Full learning cycle: reflect -> generate -> save -> record.

        Returns the generated skill if one was produced, None otherwise.
        """
        if not self.reflection.should_reflect(trace):
            return None

        reflection = self.reflection.reflect(trace)
        if reflection.generated_skill is None:
            return None

        skill = self._persist_generated_skill(reflection, trace)
        self.history.record_creation(
            skill,
            source_trace_id=trace.task_id,
            source_session_id=trace.session_id,
        )

        should_approve = auto_approve if auto_approve is not None else (
            reflection.confidence >= self.auto_approve_threshold
        )
        if should_approve:
            skill.manifest.status = SkillStatus.ACTIVE

        return skill

    # -- public API: skill refinement ----------------------------------------

    def record_skill_feedback(
        self,
        skill_name: str,
        success: bool,
        duration_ms: float = 0.0,
        session_id: str = "",
        error: str | None = None,
    ) -> list[str]:
        """Record a single execution result for a skill.

        Returns any suggested fixes for failing skills.
        """
        self.refiner.record_execution(
            skill_name, success, duration_ms, session_id, error
        )
        return self.refiner.suggest_fixes(skill_name)

    def auto_refine_skill(self, skill: Skill) -> bool:
        """Check if a skill should be promoted/demoted based on stats,
        and apply the change if warranted."""
        stats = self.refiner.get_stats(skill.manifest.name)

        if self.refiner.should_promote(skill.manifest.name):
            old_status = skill.manifest.status
            skill.manifest.status = SkillStatus.ACTIVE
            self.refiner.adjust_confidence(skill, skill.manifest.name)
            self.history.record_status_change(
                skill.manifest.name, old_status, SkillStatus.ACTIVE,
            )
            return True

        if self.refiner.should_demote(skill.manifest.name):
            old_status = skill.manifest.status
            skill.manifest.status = SkillStatus.DISABLED
            self.refiner.adjust_confidence(skill, skill.manifest.name)
            self.history.record_status_change(
                skill.manifest.name, old_status, SkillStatus.DISABLED,
            )
            return True

        return False

    def get_skill_stats(self, skill_name: str):
        return self.refiner.get_stats(skill_name)

    def get_refinement_changelog(self) -> str:
        return self.refiner.generate_changelog()

    # -- public API: history -------------------------------------------------

    def get_learning_history(self, limit: int = 20) -> list[LearningEvent]:
        return self.history.get_recent(limit)

    def get_skills_for_session(self, session_id: str) -> list[str]:
        return self.history.get_skills_for_session(session_id)

    # -- backward compat / convenience ---------------------------------------

    def generate_skill_candidate(
        self,
        task: Task,
        trace: list[dict],
        proposed_name: str | None = None,
    ) -> str | None:
        """Backward-compatible: generate a SKILL.md string from a raw trace."""
        exec_trace = self.build_trace_from_dict(task, trace)
        reflection = self.reflection.reflect(exec_trace)
        if reflection.generated_skill is None:
            return None

        gs = reflection.generated_skill
        name = proposed_name or gs.proposed_name
        frontmatter = {
            "name": name,
            "description": gs.description,
            "version": "1.0.0",
            "tags": gs.tags,
            "requires": [],
            "tools": gs.tools_used,
        }
        fm_yaml = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).strip()
        return f"---\n{fm_yaml}\n---\n\n{gs.instructions}\n"

    def save_candidate(self, skill_md_content: str, task_title: str) -> Path:
        name = self._slugify(task_title)
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(skill_md_content, encoding="utf-8")
        return skill_path

    async def propose_refinement(
        self, skill: Skill, feedback: str
    ) -> str | None:
        """Given user feedback on a skill, store it and return suggestions."""
        await self.memory.upsert(
            type(
                "MemoryEntry",
                (),
                {
                    "entry_type": MemoryEntryType.SKILL_LEARNED,
                    "title": f"Feedback on {skill.manifest.name}",
                    "content": feedback,
                    "tags": ["skill-feedback", skill.manifest.name],
                    "importance": 0.7,
                },
            )
        )
        return None

    # -- persistent sync ------------------------------------------------------

    async def sync_to_memory(self) -> int:
        """Persist all learning events as memory entries. Returns count."""
        entries = self.history.to_memory_entries()
        count = 0
        for entry in entries:
            await self.memory.upsert(entry)
            count += 1
        return count

    # -- internal -------------------------------------------------------------

    def _persist_generated_skill(
        self,
        reflection,
        trace: ExecutionTrace,
    ) -> Skill:
        gs = reflection.generated_skill
        manifest = SkillManifest(
            name=gs.proposed_name,
            description=gs.description,
            version="1.0.0",
            kind=SkillKind.NATIVE,
            tags=gs.tags,
            tools=gs.tools_used,
            status=SkillStatus.DISCOVERED,
            metadata={
                "auto_generated": True,
                "source_trace_id": str(gs.source_trace_id or ""),
                "source_task": gs.source_task_title,
                "reflection_confidence": reflection.confidence,
            },
        )
        return Skill(manifest=manifest, instructions=gs.instructions)

    @staticmethod
    def _slugify(text: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unnamed-skill"
