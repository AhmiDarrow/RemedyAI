"""Learning loop -- the self-improvement orchestrator.

Orchestrates reflection, skill generation, refinement, and procedural
memory integration. The single entry point for Remedy's "grows with you"
philosophy.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import yaml

from remedy.core.learning.lifecycle import LifecycleDecision, SkillLifecyclePolicy
from remedy.core.learning.procedural import LearningEvent, LearningHistory
from remedy.core.learning.refiner import SkillRefiner
from remedy.core.learning.reflection import (
    ExecutionTrace,
    ReflectionEngine,
    TraceStep,
)
from remedy.memory.store import MemoryStore
from remedy.models import (
    MemoryEntry,
    MemoryEntryType,
    Skill,
    SkillKind,
    SkillManifest,
    SkillStatus,
    Task,
    TaskStatus,
)
from remedy.skills.validator import SkillValidator


class LearningLoop:
    """Orchestrates the full learning cycle with lifecycle gates.

    Skills are never ACTIVE from a single lucky trace. They enter probation
    (DISCOVERED/VALIDATED), promote only after multi-session success, demote
    on failure streaks, and can be pruned when hopeless.

    Usage:
        loop = LearningLoop(skills_dir=Path("~/.remedy/skills"), memory=store)

        # After a complex task:
        trace = ExecutionTrace(task_id=task.id, title=task.title, steps=[...])
        result = loop.learn_from_trace(trace)

        # After skill execution feedback:
        loop.record_skill_feedback("my-skill", success=True, duration_ms=150)
        loop.auto_refine_skill(skill)
    """

    def __init__(
        self,
        skills_dir: Path,
        memory: MemoryStore | None = None,
        auto_approve_threshold: float = 0.8,
        *,
        stats_path: Path | str | None = None,
        registry=None,
    ) -> None:
        self.skills_dir = Path(skills_dir).expanduser().resolve()
        self.memory = memory
        # Kept for API compat; creation never auto-ACTIVEs regardless of threshold.
        self.auto_approve_threshold = auto_approve_threshold
        self.reflection = ReflectionEngine()
        # Durable stats next to skills dir by default
        default_stats = self.skills_dir.parent / "skill_stats.json"
        self.refiner = SkillRefiner(stats_path=stats_path or default_stats)
        self.lifecycle = SkillLifecyclePolicy()
        self.validator = SkillValidator()
        self.history = LearningHistory()
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._last_decision: LifecycleDecision | None = None
        # Optional SkillRegistry to re-register after learn / status changes
        self.registry = registry

    # -- public API: learning from traces ------------------------------------

    def build_trace_from_dict(
        self,
        task: Task,
        steps: list[dict],
        session_id: str | None = None,
    ) -> ExecutionTrace:
        """Build an ExecutionTrace from a task and raw step dicts.

        Accepts common key aliases (``tool`` / ``tool_name``, ``args`` /
        ``arguments``, ``summary`` / ``result``). Unknown keys are ignored;
        missing tool names fall back to ``step_N`` (logged at debug).
        """
        import logging

        log = logging.getLogger(__name__)
        trace_steps = []
        for i, s in enumerate(steps or []):
            if not isinstance(s, dict):
                log.debug("trace step %s is not a dict; coercing to empty", i)
                s = {}
            tool_name = s.get("tool_name") or s.get("tool") or s.get("name")
            if not tool_name:
                tool_name = f"step_{i}"
                log.debug("trace step %s missing tool name; using %s", i, tool_name)
            args = s.get("arguments", s.get("args", {}))
            if not isinstance(args, dict):
                args = {"value": args}
            trace_steps.append(TraceStep(
                index=i,
                tool_name=str(tool_name),
                arguments=args,
                result_summary=str(s.get("result", s.get("summary", "")))[:200],
                success=bool(s.get("success", True)),
                duration_ms=float(s.get("duration_ms", 0.0) or 0.0),
                error=s.get("error"),
            ))

        # Prefer explicit COMPLETED; otherwise infer from step successes so
        # learn_from_trace / generate_skill_candidate work without a TaskState machine.
        steps_ok = bool(trace_steps) and all(s.success for s in trace_steps)
        overall = task.status == TaskStatus.COMPLETED or (
            task.status in (TaskStatus.CREATED, TaskStatus.IN_PROGRESS, TaskStatus.QUEUED)
            and steps_ok
        )
        return ExecutionTrace(
            task_id=task.id,
            title=task.title,
            description=task.description,
            steps=trace_steps,
            overall_success=overall,
            tags=task.tags,
            session_id=session_id,
        )

    def learn_from_trace(
        self,
        trace: ExecutionTrace,
        auto_approve: bool | None = None,
    ) -> Skill | None:
        """Full learning cycle: gate -> reflect -> validate -> save (probation).

        Returns the generated skill if one was produced, None otherwise.

        ``auto_approve`` is **ignored for ACTIVE promotion**. At most VALIDATED
        is allowed when confidence is high — never ACTIVE from one trace.
        If a skill with the same name already exists, **merge** into it instead
        of spawning a near-duplicate.
        """
        self._last_decision = None
        if not self.reflection.should_reflect(trace):
            self._last_decision = LifecycleDecision(
                "reject", "Trace too short for reflection", confidence=0.1
            )
            return None

        # Pre-gate before spending reflection / disk
        from remedy.core.learning.lifecycle import compute_effort_score

        successful = sum(1 for s in trace.steps if s.success)
        effort = compute_effort_score(
            steps=trace.steps, total_duration_ms=trace.total_duration_ms
        )
        # Peek patterns lightly for gate (full reflect still runs if accepted)
        peek = self.reflection._extract_tool_sequences(trace)  # noqa: SLF001
        gate = self.lifecycle.should_accept_trace(
            step_count=trace.step_count,
            successful_steps=successful,
            overall_success=trace.overall_success,
            step_success_rate=trace.success_rate,
            has_reusable_pattern=bool(peek),
            title=trace.title,
            effort=effort,
        )
        self._last_decision = gate
        if gate.action != "accept":
            return None

        reflection = self.reflection.reflect(trace)
        if reflection.generated_skill is None:
            self._last_decision = LifecycleDecision(
                "reject",
                "Reflection produced no skill candidate",
                confidence=0.2,
                effort=effort.score,
            )
            return None

        # Blend lifecycle + reflection confidence; never auto-ACTIVE
        conf = max(gate.confidence, reflection.confidence)

        # Merge into existing same-name skill when present
        existing = self._find_existing_skill(reflection.generated_skill.proposed_name)
        if existing is not None:
            merged = self._merge_into_existing(
                existing, reflection, trace, confidence=conf, effort=effort
            )
            if merged is not None:
                return merged

        skill = self._persist_generated_skill(
            reflection, trace, confidence=conf, effort=effort
        )

        # Static validation — refuse to keep invalid candidates on disk
        meta_res = self.validator.validate_metadata(skill)
        if not meta_res.is_valid:
            self._last_decision = LifecycleDecision(
                "reject",
                "Validation failed: " + "; ".join(meta_res.errors),
                confidence=0.1,
                details={"errors": list(meta_res.errors)},
            )
            return None

        # Write to disk only as probation skill
        path = self._write_skill_md(skill)
        skill.manifest.path = str(path.parent)
        skill.manifest.metadata["skill_path"] = str(path)
        skill.manifest.metadata["lifecycle"] = "probation"
        skill.manifest.metadata["creation_gate"] = gate.reason
        skill.manifest.metadata["effort_weight"] = effort.score
        skill.manifest.metadata["effort_band"] = effort.band
        skill.manifest.metadata["effort_reasons"] = list(effort.reasons)

        self.history.record_creation(
            skill,
            source_trace_id=trace.task_id,
            source_session_id=trace.session_id,
            confidence=conf,
        )

        # Compat: auto_approve may elevate DISCOVERED → VALIDATED only
        # Hard-won high-effort skills elevate more readily (still not ACTIVE).
        elevate = (
            auto_approve
            or conf >= self.auto_approve_threshold
            or effort.is_hard_won
        )
        if elevate and skill.manifest.status == SkillStatus.DISCOVERED:
            skill.manifest.status = SkillStatus.VALIDATED
            skill.manifest.metadata["lifecycle"] = "validated-probation"
            self._write_skill_md(skill)

        # Register into live registry when available
        if self.registry is not None:
            with contextlib.suppress(Exception):
                self.registry.register(skill)

        self._last_decision = LifecycleDecision(
            "accept",
            (
                f"Skill '{skill.manifest.name}' saved as {skill.manifest.status.value} "
                f"(probation, effort={effort.band} {effort.score:.2f})"
            ),
            new_status=skill.manifest.status,
            confidence=conf,
            effort=effort.score,
        )
        return skill

    @property
    def last_lifecycle_decision(self) -> LifecycleDecision | None:
        return self._last_decision

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

    def record_skill_activation(
        self, skill_name: str, session_id: str = ""
    ) -> None:
        """Record progressive-disclosure activate (re-use signal, not execution)."""
        self.refiner.record_activation(skill_name, session_id=session_id)

    def get_reuse_metrics(self) -> dict:
        return self.refiner.get_reuse_metrics()

    def auto_refine_skill(self, skill: Skill) -> bool:
        """Promote / demote / deprecate using lifecycle policy (multi-run evidence)."""
        name = skill.manifest.name
        stats = self.refiner.get_stats(name)
        health = self.lifecycle.health_from_stats(
            skill,
            total=stats.total_executions,
            successes=stats.successes,
            failures=stats.failures,
            sessions=len(stats.execution_by_session),
            consecutive_failures=self.refiner.failure_streak(name),
            last_success_at=self.refiner.last_success_at(name),
            last_failure_at=self.refiner.last_failure_at(name),
        )
        decision = self.lifecycle.evaluate_health(health)
        self._last_decision = decision
        if decision.action in ("hold", "reject") or decision.new_status is None:
            return False
        if decision.new_status == skill.manifest.status:
            return False

        old_status = skill.manifest.status
        skill.manifest.status = decision.new_status
        skill.manifest.metadata = dict(skill.manifest.metadata or {})
        skill.manifest.metadata["lifecycle_last"] = decision.reason
        skill.manifest.metadata["lifecycle_action"] = decision.action
        self.refiner.adjust_confidence(skill, name)
        self.history.record_status_change(name, old_status, decision.new_status)
        # Persist status change when skill is on disk
        if skill.manifest.path or skill.manifest.metadata.get("skill_path"):
            with contextlib.suppress(OSError):
                self._write_skill_md(skill)
        return True

    def prune_skill(self, skill: Skill, *, remove_files: bool = False) -> bool:
        """Deprecate (and optionally delete) a hopeless skill."""
        name = skill.manifest.name
        stats = self.refiner.get_stats(name)
        health = self.lifecycle.health_from_stats(
            skill,
            total=stats.total_executions,
            successes=stats.successes,
            failures=stats.failures,
            sessions=len(stats.execution_by_session),
            consecutive_failures=self.refiner.failure_streak(name),
            last_success_at=self.refiner.last_success_at(name),
            last_failure_at=self.refiner.last_failure_at(name),
        )
        decision = self.lifecycle.evaluate_health(health)
        if decision.action != "prune" and skill.manifest.status != SkillStatus.DISABLED:
            # Allow explicit prune of DISABLED always
            if skill.manifest.status not in (
                SkillStatus.DISABLED,
                SkillStatus.DEPRECATED,
            ):
                return False

        old = skill.manifest.status
        skill.manifest.status = SkillStatus.DEPRECATED
        self.history.record_status_change(name, old, SkillStatus.DEPRECATED)
        if remove_files:
            path = skill.manifest.path or skill.manifest.metadata.get("skill_path")
            if path:
                import shutil
                from pathlib import Path as _P

                p = _P(path)
                target = p if p.is_dir() else p.parent
                if target.exists() and target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                self._write_skill_md(skill)
        return True

    def get_skill_stats(self, skill_name: str):
        return self.refiner.get_stats(skill_name)

    def get_refinement_changelog(self, skill_name: str | None = None) -> str:
        return self.refiner.generate_changelog(skill_name=skill_name)

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
        if self.memory is None:
            return None
        await self.memory.upsert(
            MemoryEntry(
                entry_type=MemoryEntryType.SKILL_LEARNED,
                title=f"Feedback on {skill.manifest.name}",
                content=feedback,
                tags=["skill-feedback", skill.manifest.name],
                importance=0.7,
            )
        )
        return None

    # -- persistent sync ------------------------------------------------------

    async def sync_to_memory(self) -> int:
        """Persist all learning events as memory entries. Returns count."""
        if self.memory is None:
            return 0
        entries = self.history.to_memory_entries()
        count = 0
        for entry in entries:
            await self.memory.upsert(entry)
            count += 1
        return count

    # -- internal -------------------------------------------------------------

    def learn_from_tool_steps(
        self,
        *,
        title: str,
        steps: list[dict],
        session_id: str | None = None,
        description: str = "",
        overall_success: bool = True,
    ) -> Skill | None:
        """Convenience for post-turn auto-learn from agent tool call dicts."""
        from uuid import uuid4

        task = Task(
            id=uuid4(),
            title=title[:120] or "tool-session",
            description=description,
            status=TaskStatus.COMPLETED if overall_success else TaskStatus.FAILED,
        )
        trace = self.build_trace_from_dict(task, steps, session_id=session_id)
        if not overall_success:
            trace.overall_success = False
        return self.learn_from_trace(trace)

    def _find_existing_skill(self, name: str) -> Skill | None:
        if self.registry is not None:
            found = self.registry.get(name)
            if found is not None:
                return found
        # Disk fallback
        path = self.skills_dir / name / "SKILL.md"
        if path.is_file():
            try:
                from remedy.skills.loader import load_skill_from_dir

                return load_skill_from_dir(path.parent)
            except Exception:
                return None
        return None

    def _merge_into_existing(
        self,
        existing: Skill,
        reflection,
        trace: ExecutionTrace,
        *,
        confidence: float,
        effort=None,
    ) -> Skill | None:
        """Append recovery/steps from new trace; bump patch version; keep status."""
        gs = reflection.generated_skill
        if gs is None:
            return None
        # Never rewrite official library / imported packs as auto_generated
        meta0 = existing.manifest.metadata or {}
        if meta0.get("source") == "library" or meta0.get("library_id"):
            return None
        if meta0.get("trust") in ("imported", "library-install", "library-update"):
            return None
        eff_score = float(getattr(effort, "score", 0.0) or 0.0)
        old_effort = float(
            (existing.manifest.metadata or {}).get("effort_weight") or 0.0
        )
        new_effort = max(old_effort, eff_score)

        # Description: prefer longer trigger-oriented text
        if gs.description and len(gs.description) > len(existing.manifest.description or ""):
            existing.manifest.description = gs.description

        # Merge tags
        tags = list(existing.manifest.tags or [])
        for t in gs.tags or []:
            if t not in tags:
                tags.append(t)
        existing.manifest.tags = tags

        # Tools union
        tools = list(existing.manifest.tools or [])
        for t in gs.tools_used or []:
            if t not in tools:
                tools.append(t)
        existing.manifest.tools = tools

        # Append merge section to instructions
        merge_block = (
            f"\n\n## Merged experience ({trace.title})\n\n"
            f"_Trace success rate: {trace.success_rate:.0%}; "
            f"effort={getattr(effort, 'band', 'n/a')}_\n\n"
        )
        # Keep only failure/recovery lines from new instructions to avoid bloat
        new_lines = []
        for line in (gs.instructions or "").splitlines():
            low = line.lower()
            if "fail" in low or "recover" in low or "error" in low:
                new_lines.append(line)
        if new_lines:
            merge_block += "\n".join(new_lines[:40]) + "\n"
        else:
            merge_block += f"- Additional successful path with tools: {', '.join(gs.tools_used[:8])}\n"
        existing.instructions = (existing.instructions or "").rstrip() + merge_block

        meta = dict(existing.manifest.metadata or {})
        meta["auto_generated"] = True
        meta["lifecycle_confidence"] = max(
            float(meta.get("lifecycle_confidence") or 0), confidence
        )
        meta["effort_weight"] = new_effort
        meta["effort_band"] = (
            "high" if new_effort >= 0.62 else getattr(effort, "band", meta.get("effort_band"))
        )
        tids = list(meta.get("source_trace_ids") or [])
        tid = str(getattr(gs, "source_trace_id", "") or trace.task_id)
        if tid and tid not in tids:
            tids.append(tid)
        meta["source_trace_ids"] = tids[-20:]
        meta["last_merge_task"] = trace.title
        existing.manifest.metadata = meta

        # Patch version bump
        try:
            from packaging.version import Version

            v = Version(existing.manifest.version or "0.1.0")
            existing.manifest.version = f"{v.major}.{v.minor}.{v.micro + 1}"
        except Exception:
            existing.manifest.version = (existing.manifest.version or "0.1.0") + "+m"

        path = self._write_skill_md(existing)
        existing.manifest.path = str(path.parent)
        if self.registry is not None:
            with contextlib.suppress(Exception):
                self.registry.register(existing)
        self.history.record_refinement(
            existing.manifest.name,
            from_version="merge",
            to_version=existing.manifest.version,
            reason=f"Merged trace '{trace.title}'",
        )
        self._last_decision = LifecycleDecision(
            "accept",
            f"Merged into existing skill '{existing.manifest.name}' "
            f"(v{existing.manifest.version}, effort={new_effort:.2f})",
            new_status=existing.manifest.status,
            confidence=confidence,
            effort=new_effort,
        )
        return existing

    def _persist_generated_skill(
        self,
        reflection,
        trace: ExecutionTrace,
        *,
        confidence: float = 0.0,
        effort=None,
    ) -> Skill:
        gs = reflection.generated_skill
        eff_score = float(getattr(effort, "score", 0.0) or 0.0)
        status = self.lifecycle.initial_status_for_learned(
            confidence, effort=eff_score
        )
        tags = list(gs.tags or [])
        if "probation" not in tags:
            tags.append("probation")
        if effort is not None and getattr(effort, "is_hard_won", False):
            if "hard-won" not in tags:
                tags.append("hard-won")
        manifest = SkillManifest(
            name=gs.proposed_name,
            description=gs.description,
            version="0.1.0",  # probation versions stay < 1.0 until promoted
            kind=SkillKind.NATIVE,
            tags=tags,
            tools=gs.tools_used,
            status=status,
            metadata={
                "auto_generated": True,
                "source_trace_id": str(gs.source_trace_id or ""),
                "source_trace_ids": [str(gs.source_trace_id or trace.task_id)],
                "source_task": gs.source_task_title,
                "source_session_id": trace.session_id,
                "reflection_confidence": reflection.confidence,
                "lifecycle_confidence": confidence,
                "lifecycle": "probation",
                "overall_trace_success": trace.overall_success,
                "trace_success_rate": trace.success_rate,
                "effort_weight": eff_score,
                "effort_band": getattr(effort, "band", "trivial"),
                "effort_reasons": list(getattr(effort, "reasons", []) or []),
            },
        )
        return Skill(manifest=manifest, instructions=gs.instructions)

    def _write_skill_md(self, skill: Skill) -> Path:
        """Write SKILL.md for a skill under skills_dir / name."""
        name = skill.manifest.name or "unnamed-skill"
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        frontmatter = {
            "name": skill.manifest.name,
            "description": skill.manifest.description,
            "version": skill.manifest.version,
            "kind": skill.manifest.kind.value if hasattr(skill.manifest.kind, "value") else str(skill.manifest.kind),
            "status": skill.manifest.status.value if hasattr(skill.manifest.status, "value") else str(skill.manifest.status),
            "tags": list(skill.manifest.tags or []),
            "tools": list(skill.manifest.tools or []),
            "metadata": dict(skill.manifest.metadata or {}),
        }
        fm_yaml = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).strip()
        body = f"---\n{fm_yaml}\n---\n\n{skill.instructions.rstrip()}\n"
        path = skill_dir / "SKILL.md"
        path.write_text(body, encoding="utf-8")
        return path

    @staticmethod
    def _slugify(text: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unnamed-skill"
