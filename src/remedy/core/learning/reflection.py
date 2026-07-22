"""Reflection engine -- deep trace analysis and pattern extraction.

After complex tasks, analyzes execution traces to:
- Identify reusable tool-call sequences
- Detect error-recovery patterns
- Extract decision points and branching logic
- Generate structured skill candidates with auto-detected names, descriptions, and tags
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID


@dataclass
class TraceStep:
    """A single step in an execution trace."""
    index: int
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    success: bool = True
    duration_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class ExecutionTrace:
    """A complete task execution trace for reflection."""
    task_id: UUID
    title: str
    description: str = ""
    steps: list[TraceStep] = field(default_factory=list)
    overall_success: bool = True
    total_duration_ms: float = 0.0
    tags: list[str] = field(default_factory=list)
    session_id: Optional[str] = None

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def success_rate(self) -> float:
        if not self.steps:
            return 1.0
        return sum(1 for s in self.steps if s.success) / len(self.steps)


@dataclass
class Reflection:
    """Output of reflecting on an execution trace."""
    trace_id: UUID
    extracted_patterns: list[ToolSequence] = field(default_factory=list)
    detected_errors: list[str] = field(default_factory=list)
    key_decision_points: list[str] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
    generated_skill: Optional[GeneratedSkill] = None
    reflection_text: str = ""
    confidence: float = 0.0


@dataclass
class ToolSequence:
    """A recurring sequence of tool calls detected in traces."""
    tools: list[str]
    frequency: int = 1
    success_rate: float = 1.0
    typical_duration_ms: float = 0.0
    description: str = ""


@dataclass
class GeneratedSkill:
    """A skill candidate generated from trace analysis."""
    proposed_name: str
    description: str
    instructions: str
    tags: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    estimated_success_rate: float = 0.8
    source_trace_id: Optional[UUID] = None
    source_task_title: str = ""
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_MIN_TRACE_LENGTH = 3
_MIN_REFLECTION_LENGTH = 200


class ReflectionEngine:
    """Analyzes execution traces to extract skills and patterns."""

    def __init__(self, min_steps: int = _MIN_TRACE_LENGTH) -> None:
        self.min_steps = min_steps

    def should_reflect(self, trace: ExecutionTrace) -> bool:
        return trace.step_count >= self.min_steps

    def reflect(self, trace: ExecutionTrace) -> Reflection:
        """Perform full reflection on an execution trace.

        Extracts patterns, errors, decisions, and generates a skill candidate
        if the trace is sufficiently complex.
        """
        if not self.should_reflect(trace):
            return Reflection(
                trace_id=trace.task_id,
                reflection_text="Trace too short for meaningful reflection.",
                confidence=0.1,
            )

        patterns = self._extract_tool_sequences(trace)
        errors = self._extract_error_patterns(trace)
        decisions = self._extract_decisions(trace)
        suggested_tools = self._suggest_reusable_tools(trace)
        generated = self._generate_skill_from_trace(trace, patterns)

        confidence = self._compute_confidence(trace, patterns, errors)

        return Reflection(
            trace_id=trace.task_id,
            extracted_patterns=patterns,
            detected_errors=errors,
            key_decision_points=decisions,
            suggested_tools=suggested_tools,
            generated_skill=generated,
            reflection_text=self._build_reflection_text(trace, patterns, errors, generated),
            confidence=confidence,
        )

    def _extract_tool_sequences(self, trace: ExecutionTrace) -> list[ToolSequence]:
        """Find recurring tool sequences in the trace (sliding window)."""
        if len(trace.steps) < 2:
            return []

        tool_names = [s.tool_name for s in trace.steps]
        sequences: dict[tuple, list[int]] = {}

        # 2-tool and 3-tool sliding windows
        for window_size in (2, 3):
            for i in range(len(tool_names) - window_size + 1):
                seq = tuple(tool_names[i : i + window_size])
                if seq not in sequences:
                    sequences[seq] = []
                sequences[seq].append(i)

        # Keep only sequences that appear more than once
        results: list[ToolSequence] = []
        for seq, positions in sequences.items():
            if len(positions) >= 2:
                successes = sum(
                    1 for p in positions
                    if all(trace.steps[p + j].success for j in range(len(seq)))
                )
                avg_duration = sum(
                    sum(trace.steps[p + j].duration_ms for j in range(len(seq)))
                    for p in positions
                ) / len(positions)

                results.append(ToolSequence(
                    tools=list(seq),
                    frequency=len(positions),
                    success_rate=successes / len(positions),
                    typical_duration_ms=avg_duration,
                    description=self._describe_sequence(list(seq)),
                ))

        return sorted(results, key=lambda x: x.frequency, reverse=True)

    def _describe_sequence(self, tools: list[str]) -> str:
        return " -> ".join(tools) + " pattern"

    def _extract_error_patterns(self, trace: ExecutionTrace) -> list[str]:
        errors: list[str] = []
        for step in trace.steps:
            if not step.success and step.error:
                context = "unknown context"
                if step.index > 0:
                    context = f"after {trace.steps[step.index - 1].tool_name}"
                errors.append(
                    f"Step {step.index + 1}: {step.tool_name} failed ({step.error}) "
                    f"{context}"
                )
        return errors

    def _extract_decisions(self, trace: ExecutionTrace) -> list[str]:
        decisions: list[str] = []
        for i, step in enumerate(trace.steps):
            if not step.success and i + 1 < len(trace.steps):
                next_step = trace.steps[i + 1]
                decisions.append(
                    f"After {step.tool_name} failure, chose {next_step.tool_name}"
                )
            if step.tool_name.startswith("decide") or step.tool_name.startswith("choose"):
                decisions.append(
                    f"Explicit decision: {step.tool_name} with {step.arguments}"
                )
        return decisions

    def _suggest_reusable_tools(self, trace: ExecutionTrace) -> list[str]:
        tool_counter = Counter(s.tool_name for s in trace.steps if s.success)
        return [tool for tool, count in tool_counter.most_common(5) if count >= 2]

    def _generate_skill_from_trace(
        self,
        trace: ExecutionTrace,
        patterns: list[ToolSequence],
    ) -> Optional[GeneratedSkill]:
        if trace.step_count < self.min_steps:
            return None

        name = self._propose_skill_name(trace)
        description = self._build_skill_description(trace)
        instructions = self._build_skill_instructions(trace, patterns)
        tags = self._suggest_tags(trace)

        return GeneratedSkill(
            proposed_name=name,
            description=description,
            instructions=instructions,
            tags=tags,
            tools_used=list(set(s.tool_name for s in trace.steps)),
            estimated_success_rate=trace.success_rate,
            source_trace_id=trace.task_id,
            source_task_title=trace.title,
        )

    def _propose_skill_name(self, trace: ExecutionTrace) -> str:
        title_slug = re.sub(r"[^a-z0-9]+", "-", trace.title.lower()).strip("-")
        primary_tool = Counter(s.tool_name for s in trace.steps).most_common(1)
        tool_prefix = primary_tool[0][0] if primary_tool else "task"
        return f"{tool_prefix}-{title_slug}"[:64]

    def _build_skill_description(self, trace: ExecutionTrace) -> str:
        steps_summary = ", ".join(
            sorted(set(s.tool_name for s in trace.steps))
        )
        return (
            f"Automates the task: '{trace.title}'. "
            f"Uses tools: {steps_summary}. "
            f"Learned from session {trace.session_id or 'unknown'}."
        )

    def _build_skill_instructions(
        self,
        trace: ExecutionTrace,
        patterns: list[ToolSequence],
    ) -> str:
        lines = [
            f"# {trace.title}",
            "",
            f"This skill was automatically generated from a successful execution.",
            "",
            "## Steps",
            "",
        ]
        for i, step in enumerate(trace.steps):
            status = "SUCCESS" if step.success else "FAILED"
            lines.append(
                f"{i + 1}. **{step.tool_name}** [{status}]"
            )
            if step.result_summary:
                lines.append(f"   - Result: {step.result_summary}")
            lines.append("")

        if patterns:
            lines.append("## Reusable Patterns")
            lines.append("")
            for p in patterns[:3]:
                lines.append(f"- `{p.description}` ({p.frequency}x, {p.success_rate:.0%} success)")

        lines.append("")
        lines.append("## Requirements")
        lines.append("")
        for tool in sorted(set(s.tool_name for s in trace.steps)):
            lines.append(f"- Tool: `{tool}`")

        return "\n".join(lines)

    def _suggest_tags(self, trace: ExecutionTrace) -> list[str]:
        tags = list(trace.tags or [])
        tags.append("auto-generated")
        tags.append("learned")

        if trace.success_rate >= 0.9:
            tags.append("high-confidence")
        elif trace.success_rate >= 0.7:
            tags.append("medium-confidence")
        else:
            tags.append("needs-review")

        for step in trace.steps:
            if step.tool_name.startswith("git"):
                tags.append("git")
            elif step.tool_name.startswith("file"):
                tags.append("filesystem")
            elif step.tool_name.startswith("memory"):
                tags.append("memory")
            elif step.tool_name.startswith("web"):
                tags.append("web")

        return list(dict.fromkeys(tags))

    def _compute_confidence(
        self,
        trace: ExecutionTrace,
        patterns: list[ToolSequence],
        errors: list[str],
    ) -> float:
        score = trace.success_rate * 0.5
        if patterns:
            score += min(len(patterns) * 0.1, 0.3)
        score -= len(errors) * 0.1
        if trace.step_count > 10:
            score += 0.1
        if trace.step_count > 20:
            score += 0.1
        return max(0.0, min(1.0, score))

    def _build_reflection_text(
        self,
        trace: ExecutionTrace,
        patterns: list[ToolSequence],
        errors: list[str],
        skill: Optional[GeneratedSkill],
    ) -> str:
        parts = [
            f"Reflection on task '{trace.title}' ({trace.step_count} steps).",
            f"Success rate: {trace.success_rate:.0%}.",
            "",
        ]
        if patterns:
            parts.append(f"Found {len(patterns)} reusable patterns.")
        if errors:
            parts.append(f"Encountered {len(errors)} errors.")
        if skill:
            parts.append(f"Generated skill candidate: {skill.proposed_name}")
        return "\n".join(parts)
