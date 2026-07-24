"""Bundled default skills ship with the package and load on discover_defaults."""

from __future__ import annotations

from pathlib import Path

from remedy.bundled_skills import bundled_skills_dir, iter_bundled_skill_dirs
from remedy.core.agent import _message_wants_tools
from remedy.skills.registry import SkillRegistry


def test_bundled_skills_exist():
    dirs = iter_bundled_skill_dirs()
    names = {d.name for d in dirs}
    assert len(dirs) >= 12  # engineering + companion skills
    assert "project-overview" in names
    assert "code-review" in names
    assert "memory-backup" in names
    assert "remember-me" in names
    assert "design-critique" in names
    for d in dirs:
        assert (d / "SKILL.md").is_file()


def test_discover_defaults_loads_bundled(tmp_path: Path):
    reg = SkillRegistry()
    home = tmp_path / "home"
    home.mkdir()
    n = reg.discover_defaults(home_dir=home)
    assert n >= 12
    assert reg.count >= 12
    assert reg.get("project-overview") is not None
    assert reg.get("remember-me") is not None
    # Seeded into user skills dir for customization
    assert (home / "skills" / "project-overview" / "SKILL.md").is_file()


def test_skills_meta_question_skips_tools():
    assert _message_wants_tools("what skills do you have?") is False
    assert _message_wants_tools("list tools") is False
    assert _message_wants_tools("review project") is True
