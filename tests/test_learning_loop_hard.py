"""Hard-path learning loop tests: no lucky ACTIVE, durable stats, multi-session.

Phase A5 of the personal-partner roadmap — failure and corruption must not
promote skills or leave skill_stats.json unusable.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from remedy.core.learning.lifecycle import SkillLifecyclePolicy
from remedy.core.learning.refiner import SkillRefiner
from remedy.core.learning.reflection import ExecutionTrace, TraceStep
from remedy.core.learning_loop import LearningLoop
from remedy.models import Skill, SkillKind, SkillManifest, SkillStatus, Task, TaskStatus


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Isolated ~/.remedy-style home for stats + skills."""
    skills = tmp_path / "skills"
    skills.mkdir()
    return tmp_path


@pytest.fixture
def loop(home: Path) -> LearningLoop:
    return LearningLoop(
        skills_dir=home / "skills",
        memory=None,
        stats_path=home / "skill_stats.json",
    )


def _clean_steps(n: int = 4) -> list[dict]:
    return [
        {"tool": f"tool_{i}", "success": True, "result": f"ok{i}"}
        for i in range(n)
    ]


def _skill(name: str, status: SkillStatus = SkillStatus.DISCOVERED, effort: float = 0.1) -> Skill:
    return Skill(
        manifest=SkillManifest(
            name=name,
            description="Test skill description long enough for validation",
            version="0.1.0",
            kind=SkillKind.NATIVE,
            status=status,
            metadata={
                "auto_generated": True,
                "effort_weight": effort,
                "lifecycle": "probation",
            },
        ),
        instructions="# steps\n" + ("do the thing\n" * 12),
    )


class TestNeverActiveFromSingleTrace:
    def test_learn_from_trace_never_active(self, loop: LearningLoop):
        task = Task(title="Build feature cleanly", status=TaskStatus.COMPLETED)
        trace = loop.build_trace_from_dict(task, _clean_steps(5), session_id="s1")
        skill = loop.learn_from_trace(trace)
        assert skill is not None
        assert skill.manifest.status != SkillStatus.ACTIVE
        assert skill.manifest.status in (SkillStatus.DISCOVERED, SkillStatus.VALIDATED)

    def test_perfect_single_session_does_not_promote_active(self, loop: LearningLoop):
        """Five successes in one session still cannot become ACTIVE."""
        skill = _skill("one-session", SkillStatus.VALIDATED)
        for _ in range(5):
            loop.record_skill_feedback(skill.manifest.name, success=True, session_id="only")
        loop.auto_refine_skill(skill)
        # May promote DISCOVERED→VALIDATED style holds, but not ACTIVE without multi-session
        assert skill.manifest.status != SkillStatus.ACTIVE
        # Policy itself must refuse
        stats = loop.get_skill_stats(skill.manifest.name)
        health = loop.lifecycle.health_from_stats(
            skill,
            total=stats.total_executions,
            successes=stats.successes,
            failures=stats.failures,
            sessions=len(stats.execution_by_session),
            consecutive_failures=0,
        )
        dec = loop.lifecycle.evaluate_health(health)
        assert dec.action != "promote" or dec.new_status != SkillStatus.ACTIVE

    def test_multi_session_can_promote_active(self, loop: LearningLoop):
        skill = _skill("multi-session", SkillStatus.VALIDATED, effort=0.2)
        for sid in ("a", "b", "c"):
            for _ in range(2):
                loop.record_skill_feedback(skill.manifest.name, success=True, session_id=sid)
        loop.auto_refine_skill(skill)
        assert skill.manifest.status == SkillStatus.ACTIVE


class TestFailedTracesDoNotCodify:
    def test_failed_overall_rejected(self, loop: LearningLoop):
        task = Task(title="Failed deploy", status=TaskStatus.FAILED)
        raw = _clean_steps(4)
        for step in raw:
            step["success"] = False
            step["error"] = "boom"
        trace = loop.build_trace_from_dict(task, raw)
        trace.overall_success = False
        assert loop.learn_from_trace(trace) is None
        assert loop.last_lifecycle_decision is not None
        assert loop.last_lifecycle_decision.action == "reject"

    def test_short_trace_rejected(self, loop: LearningLoop):
        task = Task(title="Tiny", status=TaskStatus.COMPLETED)
        trace = loop.build_trace_from_dict(
            task, [{"tool": "a", "success": True}, {"tool": "b", "success": True}]
        )
        assert loop.learn_from_trace(trace) is None


class TestSkillStatsDurability:
    def test_stats_survive_reload(self, home: Path, loop: LearningLoop):
        loop.record_skill_feedback("durable", success=True, session_id="s1")
        loop.record_skill_feedback("durable", success=False, session_id="s1", error="x")
        path = home / "skill_stats.json"
        assert path.is_file()

        loop2 = LearningLoop(
            skills_dir=home / "skills",
            memory=None,
            stats_path=path,
        )
        stats = loop2.get_skill_stats("durable")
        assert stats.total_executions == 2
        assert stats.successes == 1
        assert stats.failures == 1

    def test_corrupt_stats_file_does_not_crash(self, home: Path):
        path = home / "skill_stats.json"
        path.write_text("{not valid json!!!", encoding="utf-8")
        refiner = SkillRefiner(stats_path=path)
        assert refiner.load_stats() == 0
        # Subsequent write must produce valid JSON again
        refiner.record_execution("recovered", True, session_id="s")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "skills" in data
        assert data["skills"]["recovered"]["successes"] == 1

    def test_empty_stats_file_does_not_crash(self, home: Path):
        path = home / "skill_stats.json"
        path.write_text("", encoding="utf-8")
        refiner = SkillRefiner(stats_path=path)
        assert refiner.load_stats() == 0
        refiner.record_execution("after-empty", True)
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8"))["skills"]["after-empty"]["total_executions"] == 1

    def test_atomic_save_leaves_valid_file(self, home: Path):
        path = home / "skill_stats.json"
        refiner = SkillRefiner(stats_path=path)
        for i in range(20):
            refiner.record_execution("burst", i % 3 != 0, session_id=f"s{i % 2}")
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["skills"]["burst"]["total_executions"] == 20
        # No leftover temp files
        temps = list(home.glob("skill_stats.json.*"))
        assert temps == []


class TestLifecycleOwnsStatus:
    def test_auto_refine_does_not_resurrect_demoted(self, loop: LearningLoop):
        """Historical good rate must not override a demote decision via adjust_confidence."""
        skill = _skill("demote-me", SkillStatus.ACTIVE, effort=0.1)
        # Build a bad streak: enough volume, low rate
        for _ in range(3):
            loop.record_skill_feedback(skill.manifest.name, success=True, session_id="s1")
        for _ in range(6):
            loop.record_skill_feedback(
                skill.manifest.name, success=False, session_id="s2", error="fail"
            )
        loop.auto_refine_skill(skill)
        assert skill.manifest.status in (SkillStatus.DISABLED, SkillStatus.DEPRECATED)
        # Second refine must not bounce back to ACTIVE without new successes
        loop.auto_refine_skill(skill)
        assert skill.manifest.status != SkillStatus.ACTIVE

    def test_policy_never_promotes_discovered_on_one_success(self):
        policy = SkillLifecyclePolicy()
        skill = _skill("once", SkillStatus.DISCOVERED)
        health = policy.health_from_stats(
            skill, total=1, successes=1, failures=0, sessions=1
        )
        dec = policy.evaluate_health(health)
        assert dec.new_status != SkillStatus.ACTIVE


class TestLearnFromToolSteps:
    def test_tool_steps_helper_probation_only(self, loop: LearningLoop):
        skill = loop.learn_from_tool_steps(
            title="Wire up the API client",
            steps=_clean_steps(5),
            session_id="chat-1",
            overall_success=True,
        )
        assert skill is not None
        assert skill.manifest.status != SkillStatus.ACTIVE
        # File on disk
        path = Path(loop.skills_dir) / skill.manifest.name / "SKILL.md"
        assert path.is_file()


class TestEffortGates:
    def test_reject_failed_task_even_with_many_steps(self, loop: LearningLoop):
        steps = [
            TraceStep(index=i, tool_name=f"t{i}", success=True)
            for i in range(6)
        ]
        trace = ExecutionTrace(
            task_id=uuid4(),
            title="looked ok but failed",
            steps=steps,
            overall_success=False,
        )
        assert loop.learn_from_trace(trace) is None
