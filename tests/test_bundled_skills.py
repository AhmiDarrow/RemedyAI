"""Bundled default skills ship with the package and load on discover_defaults."""

from __future__ import annotations

from pathlib import Path

from remedy.bundled_skills import bundled_skills_dir, iter_bundled_skill_dirs
from remedy.core.agent import _message_wants_tools
from remedy.skills.registry import SkillRegistry


def test_bundled_skills_exist():
    dirs = iter_bundled_skill_dirs()
    names = {d.name for d in dirs}
    assert len(dirs) >= 14  # engineering + companion + comfyui + github
    assert "project-overview" in names
    assert "code-review" in names
    assert "memory-backup" in names
    assert "remember-me" in names
    assert "design-critique" in names
    assert "comfyui" in names
    assert "github" in names
    for d in dirs:
        assert (d / "SKILL.md").is_file()
    comfy = bundled_skills_dir() / "comfyui"
    assert (comfy / "scripts" / "comfy_client.py").is_file()
    gh = bundled_skills_dir() / "github" / "SKILL.md"
    body = gh.read_text(encoding="utf-8")
    assert "gh pr" in body or "gh " in body
    assert "force-push" in body.lower() or "force" in body.lower()


def test_discover_defaults_loads_bundled(tmp_path: Path):
    reg = SkillRegistry()
    home = tmp_path / "home"
    home.mkdir()
    n = reg.discover_defaults(home_dir=home)
    assert n >= 14
    assert reg.count >= 14
    assert reg.get("project-overview") is not None
    assert reg.get("remember-me") is not None
    assert reg.get("comfyui") is not None
    assert reg.get("github") is not None
    # Seeded into user skills dir for customization
    assert (home / "skills" / "project-overview" / "SKILL.md").is_file()
    assert (home / "skills" / "comfyui" / "SKILL.md").is_file()
    assert (home / "skills" / "github" / "SKILL.md").is_file()


def test_skills_meta_question_skips_tools():
    assert _message_wants_tools("what skills do you have?") is False
    assert _message_wants_tools("list tools") is False
    assert _message_wants_tools("review project") is True


def test_seeded_skill_refreshes_on_bundled_version_bump(tmp_path: Path):
    """Older seeded SKILL.md is replaced when package ships a higher version."""
    from remedy.skills.registry import (
        _bundled_version_newer,
        _refresh_seeded_skill_from_bundled,
    )

    bundled = tmp_path / "bundled" / "comfyui"
    user = tmp_path / "user" / "comfyui"
    bundled.mkdir(parents=True)
    user.mkdir(parents=True)
    (bundled / "SKILL.md").write_text(
        "---\nname: comfyui\nversion: 1.1.0\n---\n# New bootstrap\n",
        encoding="utf-8",
    )
    (user / "SKILL.md").write_text(
        "---\nname: comfyui\nversion: 1.0.0\n---\n# Old\n",
        encoding="utf-8",
    )
    assert _bundled_version_newer(bundled / "SKILL.md", user / "SKILL.md")
    assert _refresh_seeded_skill_from_bundled(bundled, user) is True
    body = (user / "SKILL.md").read_text(encoding="utf-8")
    assert "1.1.0" in body
    assert "New bootstrap" in body

    # Locked skills are not overwritten
    (user / "SKILL.md").write_text(
        "---\nname: comfyui\nversion: 1.0.0\n---\n# Locked\n",
        encoding="utf-8",
    )
    (user / ".user_locked").write_text("", encoding="utf-8")
    (bundled / "SKILL.md").write_text(
        "---\nname: comfyui\nversion: 9.9.9\n---\n# Even newer\n",
        encoding="utf-8",
    )
    assert _refresh_seeded_skill_from_bundled(bundled, user) is False
    assert "Locked" in (user / "SKILL.md").read_text(encoding="utf-8")
