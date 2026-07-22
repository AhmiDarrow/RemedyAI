"""Learning loop -- the self-improvement engine.

After successful complex tasks, Remedy distills steps into reusable
SKILL.md files. On repeated use or user feedback, it proposes
improvements to existing skills.

This is the core of Remedy's "grows with you" philosophy, inspired by
Hermes' autonomous learning capabilities.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from remedy.models import MemoryEntryType, Skill, SkillKind, SkillManifest, Task


class LearningLoop:
    """Analyzes task execution traces and generates skill proposals.

    Usage:
        loop = LearningLoop(skills_dir=Path("~/.remedy/skills"), memory=store)

        # After a complex task succeeds:
        skill_content = loop.generate_skill_candidate(task, trace)
        if skill_content:
            loop.save_candidate(skill_content, task.title)
    """

    def __init__(
        self,
        skills_dir: Path,
        memory,  # MemoryStore
        auto_approve_threshold: float = 0.8,
    ) -> None:
        self.skills_dir = Path(skills_dir).expanduser().resolve()
        self.memory = memory
        self.auto_approve_threshold = auto_approve_threshold
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def generate_skill_candidate(
        self,
        task: Task,
        trace: list[dict],
        proposed_name: Optional[str] = None,
    ) -> Optional[str]:
        """Analyze a completed task and produce a SKILL.md candidate.

        Args:
            task: The completed task to distill.
            trace: The sequence of actions/tool calls performed (list of dicts).
            proposed_name: Optional suggested skill name.

        Returns:
            Full SKILL.md content as a string, or None if the task
            isn't suitable for distillation.
        """
        if len(trace) < 3:
            return None

        steps = self._extract_steps(trace)
        if not steps:
            return None

        name = proposed_name or self._generate_skill_name(task)
        description = self._summarize_goal(task, steps)
        instructions = self._build_instructions(steps)

        frontmatter = {
            "name": name,
            "description": description,
            "version": "1.0.0",
            "tags": task.tags + ["auto-generated"],
            "requires": [],
            "tools": list(self._extract_tools(trace)),
        }

        fm_yaml = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).strip()
        return f"---\n{fm_yaml}\n---\n\n{instructions}\n"

    def save_candidate(self, skill_md_content: str, task_title: str) -> Path:
        """Persist a generated SKILL.md candidate for review."""
        name = self._slugify(task_title)
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(skill_md_content, encoding="utf-8")
        return skill_path

    async def propose_refinement(
        self, skill: Skill, feedback: str
    ) -> Optional[str]:
        """Given user feedback on a skill, propose an improved version.

        Currently produces a simple annotation. In future versions this
        will use the LLM to rewrite the skill.
        """
        entry = await self.memory.upsert(
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

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _extract_steps(trace: list[dict]) -> list[str]:
        steps: list[str] = []
        for i, action in enumerate(trace):
            desc = action.get("description") or action.get("tool", f"Step {i + 1}")
            result = action.get("result", "")
            summary = str(result)[:200] if result else "completed"
            steps.append(f"{i + 1}. {desc} → {summary}")
        return steps

    @staticmethod
    def _extract_tools(trace: list[dict]) -> set[str]:
        tools: set[str] = set()
        for action in trace:
            tool = action.get("tool", "")
            if tool:
                tools.add(tool)
        return tools

    @staticmethod
    def _generate_skill_name(task: Task) -> str:
        return LearningLoop._slugify(task.title)

    @staticmethod
    def _slugify(text: str) -> str:
        import re

        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return slug or "unnamed-skill"

    @staticmethod
    def _summarize_goal(task: Task, steps: list[str]) -> str:
        return f"Automated workflow for: {task.title}. " f"{len(steps)} steps."

    @staticmethod
    def _build_instructions(steps: list[str]) -> str:
        header = "# Instructions\n\n"
        header += "This skill was auto-generated by Remedy's learning loop.\n\n"
        header += "## Steps\n\n"
        return header + "\n".join(steps) + "\n"
