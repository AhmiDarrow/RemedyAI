"""Skill lifecycle: gates, effort weight, promote/demote/prune."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from remedy.core.learning.lifecycle import (
    SkillLifecyclePolicy,
    compute_effort_score,
)
from remedy.core.learning.reflection import TraceStep
from remedy.core.learning_loop import LearningLoop
from remedy.models import Skill, SkillKind, SkillManifest, SkillStatus, Task, TaskStatus


def _steps(*pairs: tuple[str, bool]) -> list[TraceStep]:
    return [
        TraceStep(index=i, tool_name=name, success=ok, duration_ms=1000.0 * (i + 1))
        for i, (name, ok) in enumerate(pairs)
    ]


def test_effort_low_for_clean_short_path():
    steps = _steps(("read", True), ("edit", True), ("test", True))
    e = compute_effort_score(steps=steps)
    assert e.score < 0.4
    assert e.band in ("trivial", "low")


def test_effort_high_for_many_failures_and_recoveries():
    # Five failed approaches then a working path
    steps = _steps(
        ("bash_exec", False),
        ("list_dir", True),
        ("bash_exec", False),
        ("file_read", False),
        ("local_discover", True),
        ("file_read", True),
        ("file_write", True),
        ("bash_exec", True),
        ("file_read", True),
        ("bash_exec", True),
    )
    for s in steps:
        if not s.success:
            s.error = "failed"
    e = compute_effort_score(steps=steps, total_duration_ms=180_000)
    assert e.is_hard_won
    assert e.failed_steps >= 3
    assert e.recovery_count >= 1
    assert "failed attempt" in " ".join(e.reasons).lower() or e.failed_steps > 0


def test_accept_hard_won_with_lower_step_rate():
    """Many fails then success — still learn; do not discard expensive work."""
    policy = SkillLifecyclePolicy()
    steps = _steps(
        ("a", False),
        ("b", False),
        ("c", False),
        ("d", False),
        ("e", True),
        ("f", True),
        ("g", True),
    )
    e = compute_effort_score(steps=steps)
    assert e.score >= 0.5
    rate = sum(1 for s in steps if s.success) / len(steps)
    # rate is ~0.43 — would fail easy gate (0.75) but pass hard gate
    assert rate < 0.75
    dec = policy.should_accept_trace(
        step_count=len(steps),
        successful_steps=3,
        overall_success=True,
        step_success_rate=rate,
        has_reusable_pattern=False,
        effort=e,
    )
    assert dec.action == "accept"
    assert dec.effort >= 0.5


def test_reject_easy_messy_trace():
    policy = SkillLifecyclePolicy()
    steps = _steps(("a", True), ("b", False), ("c", True))
    e = compute_effort_score(steps=steps)
    rate = sum(1 for s in steps if s.success) / len(steps)
    dec = policy.should_accept_trace(
        step_count=3,
        successful_steps=2,
        overall_success=True,
        step_success_rate=rate,
        has_reusable_pattern=False,
        effort=e,
    )
    assert dec.action == "reject"


def test_reject_trivial_low_diversity_tool_chain():
    """Short clean explore loops must not become skills (catalog flood)."""
    policy = SkillLifecyclePolicy()
    steps = _steps(
        ("file_read", True),
        ("file_read", True),
        ("list_dir", True),
        ("file_read", True),
    )
    e = compute_effort_score(steps=steps)
    assert e.band == "trivial"
    dec = policy.should_accept_trace(
        step_count=4,
        successful_steps=4,
        overall_success=True,
        step_success_rate=1.0,
        has_reusable_pattern=True,
        title="file_read-list_dir",
        effort=e,
    )
    assert dec.action == "reject"
    assert "trivial" in dec.reason.lower() or "diversity" in dec.reason.lower()


def test_hard_won_resists_prune():
    policy = SkillLifecyclePolicy()
    skill = Skill(
        manifest=SkillManifest(
            name="hard-skill",
            description="Hard won skill description here",
            version="0.1.0",
            kind=SkillKind.NATIVE,
            status=SkillStatus.DISABLED,
            metadata={"effort_weight": 0.9, "auto_generated": True},
        ),
        instructions="# x\n" + ("step\n" * 10),
    )
    health = policy.health_from_stats(
        skill,
        total=4,
        successes=0,
        failures=4,
        sessions=2,
        last_failure_at=datetime.now(UTC),
    )
    # Easy skill would prune at n=3 zero success; hard-won needs more
    dec = policy.evaluate_health(health)
    assert dec.action != "prune"


def test_easy_skill_prunes_faster():
    policy = SkillLifecyclePolicy()
    skill = Skill(
        manifest=SkillManifest(
            name="easy-skill",
            description="Easy skill description here",
            version="0.1.0",
            kind=SkillKind.NATIVE,
            status=SkillStatus.DISCOVERED,
            metadata={"effort_weight": 0.1, "auto_generated": True},
        ),
        instructions="# x\n" + ("step\n" * 10),
    )
    health = policy.health_from_stats(
        skill, total=3, successes=0, failures=3, sessions=1
    )
    dec = policy.evaluate_health(health)
    assert dec.action == "prune"


def test_demote_streak_higher_for_hard_won():
    policy = SkillLifecyclePolicy()
    hard = Skill(
        manifest=SkillManifest(
            name="hw",
            description="Hard won skill description long enough",
            version="1.0.0",
            status=SkillStatus.ACTIVE,
            metadata={"effort_weight": 0.85},
        ),
        instructions="# hw\n" + ("x\n" * 10),
    )
    # 3 consecutive fails demotes easy ACTIVE, not hard-won (needs 6)
    h = policy.health_from_stats(
        hard, total=10, successes=7, failures=3, sessions=3, consecutive_failures=3
    )
    dec = policy.evaluate_health(h)
    assert dec.action == "hold"

    h2 = policy.health_from_stats(
        hard, total=10, successes=4, failures=6, sessions=3, consecutive_failures=6
    )
    dec2 = policy.evaluate_health(h2)
    assert dec2.action == "demote"


@pytest.fixture
def ll(tmp_path):
    return LearningLoop(skills_dir=tmp_path / "skills", memory=None)


def test_learn_from_hard_trace_persists_effort(ll, tmp_path):
    task = Task(title="Fix flaky deploy pipeline", status=TaskStatus.COMPLETED)
    raw = [
        {"tool": "bash_exec", "success": False, "error": "permission"},
        {"tool": "list_dir", "success": True, "result": "ok"},
        {"tool": "bash_exec", "success": False, "error": "not found"},
        {"tool": "file_read", "success": False, "error": "missing"},
        {"tool": "local_discover", "success": True, "result": "found"},
        {"tool": "file_read", "success": True, "result": "cfg"},
        {"tool": "file_write", "success": True, "result": "fixed"},
        {"tool": "bash_exec", "success": True, "result": "deployed"},
    ]
    trace = ll.build_trace_from_dict(task, raw, session_id="s1")
    skill = ll.learn_from_trace(trace)
    assert skill is not None
    assert skill.manifest.status in (SkillStatus.DISCOVERED, SkillStatus.VALIDATED)
    assert skill.manifest.status != SkillStatus.ACTIVE
    meta = skill.manifest.metadata
    assert meta.get("effort_weight", 0) >= 0.5
    assert "hard-won" in (skill.manifest.tags or []) or meta.get("effort_band") in (
        "high",
        "medium",
    )
    # On disk
    path = tmp_path / "skills" / skill.manifest.name / "SKILL.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "probation" in text.lower() or "effort" in text.lower()


def test_learn_rejects_failed_overall(ll):
    task = Task(title="Broken", status=TaskStatus.FAILED)
    raw = [
        {"tool": "a", "success": True},
        {"tool": "b", "success": True},
        {"tool": "c", "success": True},
    ]
    # Force overall failure via steps + status
    task.status = TaskStatus.FAILED
    trace = ll.build_trace_from_dict(task, raw)
    # build_trace may still set overall from steps — override
    trace.overall_success = False
    assert ll.learn_from_trace(trace) is None


# --- never-used auto-learned skills age out ----------------------------------


def _unused_skill(status: SkillStatus, created_days_ago: int | None, **meta) -> Skill:
    from datetime import timedelta

    md = {"auto_generated": True, "effort_weight": 0.1, **meta}
    if created_days_ago is not None:
        md["created_at"] = (datetime.now(UTC) - timedelta(days=created_days_ago)).isoformat()
    return Skill(
        manifest=SkillManifest(
            name="unused-skill",
            description="An auto-learned skill nobody ever used",
            version="0.1.0",
            kind=SkillKind.NATIVE,
            status=status,
            metadata=md,
        ),
        instructions="# x\n" + ("step\n" * 10),
    )


def test_stale_unused_auto_skill_demotes_to_disabled():
    from remedy.core.learning.lifecycle import STALE_UNUSED_DAYS

    policy = SkillLifecyclePolicy()
    skill = _unused_skill(SkillStatus.DISCOVERED, STALE_UNUSED_DAYS + 1)
    h = policy.health_from_stats(skill, total=0, successes=0, failures=0, sessions=0)
    assert h.activations == 0 and h.created_at is not None
    dec = policy.evaluate_health(h)
    assert dec.action == "demote"
    assert dec.new_status == SkillStatus.DISABLED
    assert "never used" in dec.reason


def test_recently_created_unused_skill_holds():
    policy = SkillLifecyclePolicy()
    skill = _unused_skill(SkillStatus.DISCOVERED, 2)
    h = policy.health_from_stats(skill, total=0, successes=0, failures=0, sessions=0)
    assert policy.evaluate_health(h).action == "hold"


def test_activated_unused_skill_is_not_stale():
    policy = SkillLifecyclePolicy()
    skill = _unused_skill(SkillStatus.VALIDATED, 60)
    h = policy.health_from_stats(
        skill, total=0, successes=0, failures=0, sessions=0, activations=1
    )
    assert policy.evaluate_health(h).action == "hold"


def test_unused_skill_without_any_creation_date_holds():
    policy = SkillLifecyclePolicy()
    skill = _unused_skill(SkillStatus.DISCOVERED, None)
    h = policy.health_from_stats(skill, total=0, successes=0, failures=0, sessions=0)
    assert h.created_at is None
    assert policy.evaluate_health(h).action == "hold"


def test_created_at_falls_back_to_skill_md_mtime(tmp_path):
    import os
    from datetime import timedelta

    from remedy.core.learning.lifecycle import STALE_UNUSED_DAYS

    d = tmp_path / "unused-skill"
    d.mkdir()
    md = d / "SKILL.md"
    md.write_text("---\nname: unused-skill\n---\nbody\n", encoding="utf-8")
    old = (datetime.now(UTC) - timedelta(days=STALE_UNUSED_DAYS + 5)).timestamp()
    os.utime(md, (old, old))
    skill = _unused_skill(SkillStatus.DISCOVERED, None, skill_path=str(md))
    policy = SkillLifecyclePolicy()
    h = policy.health_from_stats(skill, total=0, successes=0, failures=0, sessions=0)
    assert h.created_at is not None
    assert policy.evaluate_health(h).action == "demote"


def test_stale_disabled_prunes_via_status_changed_at():
    from datetime import timedelta

    from remedy.core.learning.lifecycle import PRUNE_DISABLED_AFTER_DAYS

    policy = SkillLifecyclePolicy()
    changed = (datetime.now(UTC) - timedelta(days=PRUNE_DISABLED_AFTER_DAYS + 1)).isoformat()
    skill = _unused_skill(SkillStatus.DISABLED, 90, lifecycle_changed_at=changed)
    h = policy.health_from_stats(skill, total=0, successes=0, failures=0, sessions=0)
    assert h.status_changed_at is not None and h.last_failure_at is None
    dec = policy.evaluate_health(h)
    assert dec.action == "prune"
    assert dec.new_status == SkillStatus.DEPRECATED


def test_freshly_disabled_skill_is_not_pruned_yet():
    policy = SkillLifecyclePolicy()
    skill = _unused_skill(
        SkillStatus.DISABLED, 90, lifecycle_changed_at=datetime.now(UTC).isoformat()
    )
    h = policy.health_from_stats(skill, total=0, successes=0, failures=0, sessions=0)
    assert policy.evaluate_health(h).action == "hold"


def test_tick_demotes_then_retires_stale_unused_skill(tmp_path):
    """Path for old never-used skills: DISABLED, then DEPRECATED; files stay on disk."""
    from datetime import timedelta

    from remedy.core.learning.lifecycle import STALE_UNUSED_DAYS

    loop = LearningLoop(skills_dir=tmp_path / "skills", memory=None)
    skill = _unused_skill(SkillStatus.DISCOVERED, STALE_UNUSED_DAYS + 1)
    path = loop._write_skill_md(skill)
    skill.manifest.metadata["skill_path"] = str(path)

    assert loop.auto_refine_skill(skill) is True
    assert skill.manifest.status == SkillStatus.DISABLED
    assert skill.manifest.metadata.get("lifecycle_changed_at")
    assert path.exists()

    # Nothing happens until the disabled window elapses...
    assert loop.auto_refine_skill(skill) is False
    # ...then it retires — still on disk, just hidden.
    skill.manifest.metadata["lifecycle_changed_at"] = (
        datetime.now(UTC) - timedelta(days=400)
    ).isoformat()
    assert loop.auto_refine_skill(skill) is True
    assert skill.manifest.status == SkillStatus.DEPRECATED
    assert path.exists()
    assert "status: deprecated" in path.read_text(encoding="utf-8")
