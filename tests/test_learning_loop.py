"""Tests for the learning loop."""

import pytest

from remedy.core.learning.reflection import ExecutionTrace, ReflectionEngine, TraceStep
from remedy.core.learning_loop import LearningLoop
from remedy.models import Task


@pytest.fixture
def ll(tmp_path):
    skills_dir = tmp_path / "skills"
    return LearningLoop(skills_dir=skills_dir, memory=None)


class TestLearningLoop:
    def test_detect_too_short_traces(self, ll):
        task = Task(title="Simple thing")
        trace = [{"tool": "echo", "result": "ok"}]
        result = ll.generate_skill_candidate(task, trace)
        assert result is None

    def test_generate_from_longer_trace(self, ll):
        task = Task(title="Build a thing step by step")
        trace = [
            {"tool": "read_file", "result": "found config"},
            {"tool": "edit_file", "result": "modified"},
            {"tool": "run_tests", "result": "all passed"},
        ]
        content = ll.generate_skill_candidate(task, trace)
        assert content is not None
        assert "build-a-thing-step-by-step" in content
        assert "read_file" in content
        assert "all passed" in content

    def test_generate_with_proposed_name(self, ll):
        task = Task(title="Do the dance")
        trace = [
            {"tool": "step1", "result": "ok"},
            {"tool": "step2", "result": "ok"},
            {"tool": "step3", "result": "ok"},
        ]
        content = ll.generate_skill_candidate(task, trace, proposed_name="dance-skill")
        assert "dance-skill" in content
        assert "Do the dance" in content  # description still references task title

    def test_save_candidate(self, ll):
        content = "---\nname: saved-skill\ndescription: test\n---\n\n# Test"
        path = ll.save_candidate(content, "Saved Skill")
        assert path.exists()
        assert path.name == "SKILL.md"
        assert "saved-skill" in path.read_text()
        assert path.parent.name == "saved-skill"

    def test_slugify(self, ll):
        assert ll._slugify("Hello World! @#$") == "hello-world"
        assert ll._slugify("  Spaces  everywhere  ") == "spaces-everywhere"
        assert ll._slugify("___") == "unnamed-skill"

    def test_write_skill_md_blocks_path_traversal(self, ll, tmp_path):
        """Learned skills must never write outside skills_dir."""
        from remedy.models import Skill, SkillKind, SkillManifest, SkillStatus

        evil = Skill(
            manifest=SkillManifest(
                name="../../escape-me",
                description="evil",
                kind=SkillKind.NATIVE,
                status=SkillStatus.DISCOVERED,
            ),
            instructions="# nope\n",
        )
        path = ll._write_skill_md(evil)
        root = ll.skills_dir.resolve()
        assert path.resolve().is_relative_to(root)
        assert ".." not in path.parts
        assert evil.manifest.name != "../../escape-me"
        assert not (tmp_path / "escape-me").exists()

    def test_extract_tools(self):
        engine = ReflectionEngine()
        trace = ExecutionTrace(
            task_id=None, title="test",
            steps=[
                TraceStep(index=0, tool_name="read_file"),
                TraceStep(index=1, tool_name="edit_file"),
                TraceStep(index=2, tool_name="read_file"),
            ],
        )
        tools = engine._suggest_reusable_tools(trace)
        assert "read_file" in tools  # reused, appears 2x
        assert "edit_file" not in tools  # only 1x

    def test_extract_steps(self):
        engine = ReflectionEngine()
        trace = ExecutionTrace(
            task_id=None, title="test",
            steps=[
                TraceStep(index=0, tool_name="read_config", success=True, error=None),
                TraceStep(index=1, tool_name="write_file", success=False, error="permission denied"),
            ],
        )
        errors = engine._extract_error_patterns(trace)
        assert len(errors) == 1
        assert "permission denied" in errors[0]


class TestClosedLoopSignals:
    """Activation from auto-suggest, and creation stamps, feed the lifecycle."""

    def test_record_skill_activation_from_auto_suggest_path(self, ll):
        from types import SimpleNamespace

        reg_marks: list[str] = []
        reg = SimpleNamespace(mark_activated=reg_marks.append)
        runtime = SimpleNamespace(skills=reg, _session_id="sess-9")
        runtime._get_learning_loop = lambda: ll

        # Same three calls the auto-suggest injection site makes after a match.
        name = "deploy-checklist"
        runtime.skills.mark_activated(name)
        runtime._get_learning_loop().record_skill_activation(
            name, session_id=str(getattr(runtime, "_session_id", "") or "")
        )
        runtime._turn_auto_suggested_skill = name

        assert reg_marks == [name]
        stats = ll.get_skill_stats(name)
        assert stats.activations == 1
        assert stats.activation_sessions == {"sess-9": 1}
        assert stats.total_executions == 0  # activation is not execution
        assert runtime._turn_auto_suggested_skill == name

    def test_persisted_skill_carries_created_at(self, ll):
        from datetime import datetime
        from uuid import uuid4

        trace = ExecutionTrace(
            task_id=uuid4(),
            title="write then test a module",
            session_id="s1",
            steps=[
                TraceStep(0, "file_write", {}, "ok", True),
                TraceStep(1, "bash_exec", {}, "ok", True),
                TraceStep(2, "file_read", {}, "ok", True),
                TraceStep(3, "bash_exec", {}, "ok", True),
            ],
            overall_success=True,
        )
        refl = ll.reflection.reflect(trace)
        assert refl.generated_skill is not None
        skill = ll._persist_generated_skill(refl, trace, confidence=0.6)
        stamp = skill.manifest.metadata["created_at"]
        assert datetime.fromisoformat(stamp).tzinfo is not None

    def test_auto_refine_stamps_lifecycle_changed_at(self, ll):
        from remedy.models import Skill, SkillManifest, SkillStatus

        skill = Skill(
            manifest=SkillManifest(
                name="shaky-skill",
                description="A learned skill that keeps failing",
                status=SkillStatus.VALIDATED,
                metadata={"auto_generated": True},
            ),
            instructions="x",
        )
        for _ in range(3):
            ll.record_skill_feedback("shaky-skill", False)
        assert ll.auto_refine_skill(skill) is True
        assert skill.manifest.status == SkillStatus.DISABLED
        assert skill.manifest.metadata["lifecycle_changed_at"]
