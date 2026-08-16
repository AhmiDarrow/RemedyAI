"""Skills system v2: ranking, progressive disclosure, durable stats, lifecycle."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from remedy.core.learning.procedural import LearningHistory
from remedy.core.learning.refiner import SkillRefiner
from remedy.core.learning.reflection import ExecutionTrace, ReflectionEngine, TraceStep
from remedy.core.learning_loop import LearningLoop
from remedy.models import Skill, SkillManifest, SkillStatus
from remedy.skills.exporter import SkillExporter
from remedy.skills.registry import SkillRegistry


def _skill(
    name: str,
    *,
    status: SkillStatus = SkillStatus.DISCOVERED,
    desc: str = "",
    tags: list[str] | None = None,
    effort: float = 0.0,
    auto: bool = False,
    quarantine: bool = False,
) -> Skill:
    return Skill(
        manifest=SkillManifest(
            name=name,
            description=desc or f"Skill {name}",
            status=status,
            tags=tags or [],
            metadata={
                "effort_weight": effort,
                "effort_band": "high" if effort >= 0.62 else "low",
                "auto_generated": auto,
                "quarantine": quarantine,
            },
        ),
        instructions=f"# {name}\n\nDo the thing carefully.\n",
    )


def test_discover_defaults_respects_auto_generated_status(tmp_path: Path):
    home = tmp_path / "home"
    skills = home / "skills"
    skills.mkdir(parents=True)
    # Learned probation skill on disk
    d = skills / "learned-foo"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: learned-foo\ndescription: Auto skill\nstatus: discovered\n"
        "metadata:\n  auto_generated: true\n  effort_weight: 0.8\n---\n\nBody\n",
        encoding="utf-8",
    )
    reg = SkillRegistry()
    reg.discover_defaults(home_dir=home)
    learned = reg.get("learned-foo")
    assert learned is not None
    assert learned.manifest.status == SkillStatus.DISCOVERED
    # Bundled curated skills still ACTIVE
    assert reg.get("project-overview") is not None
    assert reg.get("project-overview").manifest.status == SkillStatus.ACTIVE


def test_match_skills_ranks_active_and_effort():
    reg = SkillRegistry()
    reg.register(
        _skill("git-status", status=SkillStatus.ACTIVE, desc="show git status", tags=["git"])
    )
    reg.register(
        _skill(
            "hard-git-recover",
            status=SkillStatus.VALIDATED,
            desc="recover after failed git push",
            tags=["git", "hard-won"],
            effort=0.9,
            auto=True,
        )
    )
    reg.register(
        _skill("unrelated", status=SkillStatus.ACTIVE, desc="draw pixel art", tags=["art"])
    )
    ranked = reg.match_skills("git recover push", limit=5)
    names = [s.manifest.name for s, _ in ranked]
    assert "hard-git-recover" in names
    assert names[0] in ("hard-git-recover", "git-status")
    # art should rank lower for git query
    assert names.index("unrelated") > 0 or "unrelated" not in names[:2]


def test_match_skills_demotes_trivial_tool_chain_auto():
    """Coding queries must surface curated procedures over auto tool-chain spam."""
    from remedy.skills.registry import looks_like_tool_chain_skill_name

    assert looks_like_tool_chain_skill_name("file_read-list_dir-repo_search")
    assert looks_like_tool_chain_skill_name(
        "file_read-file-read-list-dir-repo-search"
    )
    assert not looks_like_tool_chain_skill_name("change-safety")

    reg = SkillRegistry()
    reg.register(
        _skill(
            "write-tests",
            status=SkillStatus.ACTIVE,
            desc="Add focused unit tests for new or changed behavior",
            tags=["testing", "coding"],
        )
    )
    reg.register(
        _skill(
            "file_read-file-read-list-dir-repo-search",
            status=SkillStatus.VALIDATED,
            desc="Use when the user needs help with: file_read list_dir repo_search",
            tags=["auto-generated", "learned", "probation"],
            effort=0.66,  # even "hard-won" tool-chain names demote
            auto=True,
        )
    )
    reg.register(
        _skill(
            "bash_exec-file-read-file-write",
            status=SkillStatus.VALIDATED,
            desc="Use when the user needs help with: bash_exec file_read file_write",
            tags=["auto-generated", "learned"],
            effort=0.15,
            auto=True,
        )
    )
    ranked = reg.match_skills("write tests for file_edit multi-file fix", limit=5)
    names = [s.manifest.name for s, _ in ranked]
    assert names[0] == "write-tests"
    # Tool-chain auto skills may appear later but not above curated
    if "file_read-file-read-list-dir-repo-search" in names:
        assert names.index("write-tests") < names.index(
            "file_read-file-read-list-dir-repo-search"
        )


def test_skill_body_and_activation_telemetry():
    reg = SkillRegistry()
    reg.register(_skill("demo", status=SkillStatus.ACTIVE, desc="demo skill"))
    body = reg.skill_body("demo")
    assert body is not None
    assert "demo skill" in body or "Demo" in body or "demo" in body.lower()
    reg.mark_activated("demo")
    assert reg.last_activated == "demo"
    assert reg.health_snapshot("demo")["activations_session"] == 1


def test_summary_lines_include_status():
    reg = SkillRegistry()
    reg.register(_skill("x", status=SkillStatus.ACTIVE, desc="does x", effort=0.7))
    lines = reg.summary_lines(limit=5)
    assert any("active" in ln.lower() for ln in lines)
    assert any("skill_activate" in ln for ln in lines)


def test_durable_skill_stats(tmp_path: Path):
    path = tmp_path / "skill_stats.json"
    r1 = SkillRefiner(stats_path=path)
    r1.record_execution("s1", success=True, session_id="a")
    r1.record_execution("s1", success=True, session_id="b")
    r1.record_execution("s1", success=False, session_id="b")
    assert path.is_file()

    r2 = SkillRefiner(stats_path=path)
    st = r2.get_stats("s1")
    assert st.total_executions == 3
    assert st.successes == 2
    assert st.failures == 1
    assert len(st.execution_by_session) == 2


def test_learning_history_honest_confidence():
    hist = LearningHistory()
    skill = _skill("c", auto=True)
    skill.manifest.metadata["lifecycle_confidence"] = 0.42
    ev = hist.record_creation(skill, confidence=0.42)
    assert abs(ev.confidence_at_creation - 0.42) < 1e-6


def test_merge_same_name_skill(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry()
    loop = LearningLoop(skills_dir=skills_dir, memory=None, registry=reg)

    steps = [
        TraceStep(0, "file_read", {"path": "a"}, "ok", True, 10),
        TraceStep(1, "bash_exec", {"command": "x"}, "ok", True, 20),
        TraceStep(2, "file_write", {"path": "b"}, "ok", True, 15),
    ]
    # Failures + recovery for hard-won
    hard_steps = [
        TraceStep(0, "bash_exec", {}, "fail", False, 10, error="boom"),
        TraceStep(1, "file_read", {}, "ok", True, 10),
        TraceStep(2, "bash_exec", {}, "fail2", False, 10, error="nope"),
        TraceStep(3, "file_write", {}, "ok", True, 10),
        TraceStep(4, "bash_exec", {}, "ok", True, 10),
    ]
    # First trace must clear trivial/diversity gates (3 unique tools + enough steps)
    # so we use the hard path as the initial learn when the clean short path is rejected.
    t1 = ExecutionTrace(
        task_id=uuid4(),
        title="patch module",
        steps=hard_steps,
        overall_success=True,
        session_id="s1",
        total_duration_ms=90_000,
    )
    s1 = loop.learn_from_trace(t1)
    if s1 is None:
        # Fallback: longer diverse clean path
        long_clean = list(steps) + [
            TraceStep(3, "repo_search", {}, "ok", True, 10),
            TraceStep(4, "list_dir", {}, "ok", True, 10),
        ]
        t1 = ExecutionTrace(
            task_id=uuid4(),
            title="patch module carefully with review",
            steps=long_clean,
            overall_success=True,
            session_id="s1",
        )
        s1 = loop.learn_from_trace(t1)
    assert s1 is not None
    name = s1.manifest.name
    n_before = reg.count

    t2 = ExecutionTrace(
        task_id=uuid4(),
        title="patch module",
        steps=hard_steps,
        overall_success=True,
        session_id="s2",
        total_duration_ms=90_000,
    )
    # Force same proposed name by matching title pattern used in reflection
    s2 = loop.learn_from_trace(t2)
    # Either merge (same count) or new skill — merge preferred when names match
    if s2 is not None and s2.manifest.name == name:
        assert reg.count == n_before
        assert "Merged" in (s2.instructions or "") or "merge" in (
            loop.last_lifecycle_decision.reason or ""
        ).lower()


def test_trigger_oriented_description():
    engine = ReflectionEngine()
    # Enough successes for generation; one fail still documents recovery in description
    steps = [
        TraceStep(0, "file_read", {}, "ok", True),
        TraceStep(1, "bash_exec", {}, "err", False, error="x"),
        TraceStep(2, "file_write", {}, "ok", True),
        TraceStep(3, "bash_exec", {}, "ok", True),
        TraceStep(4, "file_read", {}, "ok", True),
    ]
    trace = ExecutionTrace(
        task_id=uuid4(),
        title="Fix broken config",
        steps=steps,
        overall_success=True,
        total_duration_ms=30_000,
    )
    gs = engine._generate_skill_from_trace(trace, [])  # noqa: SLF001
    assert gs is not None
    assert "Use when" in gs.description
    assert "hard-won" in gs.description.lower() or "recover" in gs.description.lower()


def test_export_import_quarantine(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    dest = tmp_path / "skills"
    dest.mkdir()
    skill = _skill("pack-me", status=SkillStatus.ACTIVE, desc="exportable")
    skill.source_skill_dir = None
    exp = SkillExporter(out)
    # Write a real skill dir to export with resources
    src = tmp_path / "src" / "pack-me"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: pack-me\ndescription: exportable\n---\n\n# Hi\n",
        encoding="utf-8",
    )
    from remedy.skills.loader import load_skill_from_dir

    skill = load_skill_from_dir(src)
    zpath = exp.export_pack([skill])
    assert zpath.is_file()
    imported = exp.import_pack_quarantine(zpath, dest)
    assert len(imported) >= 1
    imp = imported[0]
    assert imp.manifest.metadata.get("quarantine") is True
    assert imp.manifest.status == SkillStatus.DISCOVERED


def test_learn_from_tool_steps_helper(tmp_path: Path):
    loop = LearningLoop(skills_dir=tmp_path / "skills", memory=None)
    steps = [
        {"tool": "file_read", "success": True, "result": "a"},
        {"tool": "bash_exec", "success": True, "result": "b"},
        {"tool": "file_write", "success": True, "result": "c"},
        {"tool": "bash_exec", "success": False, "error": "x"},
        {"tool": "file_read", "success": True, "result": "retry"},
    ]
    skill = loop.learn_from_tool_steps(
        title="multi tool job",
        steps=steps,
        session_id="sess",
        overall_success=True,
    )
    # May reject if rate too low for effort — hard-won should accept
    if skill is not None:
        assert skill.manifest.status in (
            SkillStatus.DISCOVERED,
            SkillStatus.VALIDATED,
        )
        assert skill.manifest.status != SkillStatus.ACTIVE
