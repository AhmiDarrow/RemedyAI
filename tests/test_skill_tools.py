"""skill_activate / skill_run / skill_search as agent tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.approvals import APPROVALS
from remedy.models import AgentConfig, Skill, SkillManifest, SkillStatus, ToolCall


@pytest.fixture
def runtime(tmp_path: Path):
    cfg = AgentConfig(
        name="test",
        home_dir=str(tmp_path / "home"),
        project_path=str(tmp_path / "proj"),
    )
    (tmp_path / "proj").mkdir()
    (tmp_path / "home").mkdir()
    rt = BasicRuntime(cfg, memory=None)
    # Ensure ask mode for approval tests
    APPROVALS.set_mode("ask")
    return rt


def _reg_skill(rt: BasicRuntime, name: str, *, quarantine: bool = False, scripts=None):
    skill = Skill(
        manifest=SkillManifest(
            name=name,
            description=f"Skill {name} for testing",
            status=SkillStatus.ACTIVE,
            metadata={"quarantine": quarantine, "trust": "imported" if quarantine else "bundled"},
        ),
        instructions=f"# {name}\n\nDo the thing.\n" + ("x" * 100),
        scripts=scripts or [],
        source_skill_dir=str(Path(rt.config.home_dir) / "skills" / name),
    )
    d = Path(skill.source_skill_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    if scripts:
        (d / "scripts").mkdir(exist_ok=True)
        for s in scripts:
            p = d / s
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("print('ok')\n", encoding="utf-8")
    rt.skills.register(skill)
    return skill


@pytest.mark.asyncio
async def test_skill_activate_returns_body(runtime):
    _reg_skill(runtime, "demo-activate")
    res = await runtime.call_tool(
        ToolCall(tool_name="skill_activate", arguments={"skill": "demo-activate"})
    )
    assert res.success
    assert "demo-activate" in str(res.data).lower() or "Do the thing" in str(res.data) or "#" in str(res.data)


@pytest.mark.asyncio
async def test_skill_activate_name_alias_no_double_name_crash(runtime):
    """Regression: ToolRegistry.execute(name=…) + args name= → TypeError.

    Models often pass both skill= and name=; must not raise.
    """
    _reg_skill(runtime, "demo-activate")
    res = await runtime.call_tool(
        ToolCall(
            tool_name="skill_activate",
            arguments={
                "skill": "demo-activate",
                "name": "tool-test-desktop-txt",  # free-form alias from model
                "include_references": False,
            },
        )
    )
    assert res.success, res.error
    text = str(res.data or "")
    assert "multiple values" not in text.lower()
    assert "Do the thing" in text or "demo-activate" in text.lower() or "#" in text


@pytest.mark.asyncio
async def test_skill_activate_missing(runtime):
    res = await runtime.call_tool(
        ToolCall(tool_name="skill_activate", arguments={"skill": "nope-xyz"})
    )
    text = str(res.data or res.error or "")
    assert "not found" in text.lower() or "SKILL_NOT_FOUND" in text or not res.success


@pytest.mark.asyncio
async def test_skill_run_quarantine_blocked(runtime):
    _reg_skill(runtime, "bad-import", quarantine=True, scripts=["scripts/run.py"])
    APPROVALS.set_mode("auto")  # even auto must not run quarantine
    res = await runtime.call_tool(
        ToolCall(tool_name="skill_run", arguments={"skill": "bad-import"})
    )
    text = str(res.data or res.error or "")
    assert "quarantine" in text.lower() or "QUARANTINE" in text


@pytest.mark.asyncio
async def test_skill_activate_quarantine_blocked(runtime):
    """Untrusted packs must not inject SKILL.md until the owner Trusts them."""
    _reg_skill(runtime, "bad-import", quarantine=True)
    res = await runtime.call_tool(
        ToolCall(tool_name="skill_activate", arguments={"skill": "bad-import"})
    )
    text = str(res.data or res.error or "")
    assert "quarantine" in text.lower() or "QUARANTINE" in text


@pytest.mark.asyncio
async def test_skill_run_requires_approval(runtime, monkeypatch):
    _reg_skill(runtime, "runner", scripts=["scripts/run.py"])
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "approval_mode": "ask"},
    )
    APPROVALS.set_mode("ask")
    res = await runtime.call_tool(
        ToolCall(tool_name="skill_run", arguments={"skill": "runner"})
    )
    text = str(res.data or res.error or "")
    assert "APPROVAL_REQUIRED" in text


@pytest.mark.asyncio
async def test_skill_search(runtime):
    _reg_skill(runtime, "git-helper")
    res = await runtime.call_tool(
        ToolCall(tool_name="skill_search", arguments={"query": "git", "limit": 5})
    )
    assert res.success
    assert "git" in str(res.data).lower() or "git-helper" in str(res.data)
