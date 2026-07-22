"""Tests for the agentskills.io skill loader."""

from pathlib import Path

import pytest

from remedy.models import SkillKind, SkillStatus
from remedy.skills.loader import (
    SkillLoadError,
    _build_manifest,
    _parse_skill_md,
    discover_skills,
    load_skill_from_dir,
    load_skill_from_file,
)

SAMPLE_SKILL_MD = """---
name: test-skill
description: A skill for testing
version: 1.2.3
author: Tester
tags:
  - test
  - example
requires:
  - requests
tools:
  - echo
---

# Test Skill

This is the instruction body.

## Steps

1. Do this.
2. Do that.
"""


class TestParseSkillMd:
    def test_parse_with_frontmatter(self, tmp_path):
        path = tmp_path / "SKILL.md"
        path.write_text(SAMPLE_SKILL_MD, encoding="utf-8")
        fm, body = _parse_skill_md(path)
        assert fm["name"] == "test-skill"
        assert fm["version"] == "1.2.3"
        assert "test" in fm["tags"]
        assert "Test Skill" in body
        assert "Do this" in body

    def test_parse_no_frontmatter(self, tmp_path):
        path = tmp_path / "SKILL.md"
        path.write_text("# Just markdown\n\nNo frontmatter here.", encoding="utf-8")
        fm, body = _parse_skill_md(path)
        assert fm == {}
        assert "Just markdown" in body

    def test_parse_empty_frontmatter(self, tmp_path):
        path = tmp_path / "SKILL.md"
        path.write_text("---\n---\n\nMarkdown body", encoding="utf-8")
        fm, body = _parse_skill_md(path)
        assert fm == {}
        assert "Markdown body" in body

    def test_parse_invalid_yaml_frontmatter(self, tmp_path):
        path = tmp_path / "SKILL.md"
        path.write_text("---\n\tbad: [yaml\n---\n\nBody", encoding="utf-8")
        with pytest.raises(SkillLoadError, match="Invalid YAML frontmatter"):
            _parse_skill_md(path)


class TestBuildManifest:
    def test_builds_from_frontmatter(self, tmp_path):
        skill_md = tmp_path / "subdir" / "SKILL.md"
        skill_md.parent.mkdir()
        skill_md.write_text("---\nname: my-skill\ndescription: Does stuff\n---\n\nBody")
        fm, _ = _parse_skill_md(skill_md)
        manifest = _build_manifest(fm, SkillKind.NATIVE, skill_md)
        assert manifest.name == "my-skill"
        assert manifest.kind == SkillKind.NATIVE
        assert manifest.status == SkillStatus.DISCOVERED

    def test_missing_name_raises(self, tmp_path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\ndescription: No name here\n---\n\nBody")
        fm, _ = _parse_skill_md(skill_md)
        with pytest.raises(SkillLoadError, match="missing required 'name'"):
            _build_manifest(fm, SkillKind.NATIVE, skill_md)

    def test_missing_description_raises(self, tmp_path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: lonely\n---\n\nBody")
        fm, _ = _parse_skill_md(skill_md)
        with pytest.raises(SkillLoadError, match="missing required 'description'"):
            _build_manifest(fm, SkillKind.NATIVE, skill_md)


class TestLoadSkillFromDir:
    def test_loads_valid_skill(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(SAMPLE_SKILL_MD, encoding="utf-8")

        skill = load_skill_from_dir(skill_dir)
        assert skill.manifest.name == "test-skill"
        assert skill.manifest.version == "1.2.3"
        assert "Do this" in skill.instructions
        assert skill.source_skill_dir == str(skill_dir.resolve())

    def test_load_missing_dir(self):
        with pytest.raises(SkillLoadError, match="not found"):
            load_skill_from_dir(Path("/nonexistent/skill"))

    def test_load_no_skill_md(self, tmp_path):
        empty = tmp_path / "empty-dir"
        empty.mkdir()
        with pytest.raises(SkillLoadError, match="No SKILL.md found"):
            load_skill_from_dir(empty)

    def test_load_with_scripts_and_references(self, tmp_path):
        skill_dir = tmp_path / "rich-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(SAMPLE_SKILL_MD, encoding="utf-8")

        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "run.py").write_text("print('hello')")
        (scripts_dir / "sub").mkdir(parents=True)
        (scripts_dir / "sub" / "helper.py").write_text("pass")

        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "api.md").write_text("# API Reference")

        skill = load_skill_from_dir(skill_dir)
        assert len(skill.scripts) == 2
        assert "run.py" in skill.scripts[0]
        assert len(skill.references) == 1
        assert "api.md" in skill.references[0]

    def test_load_from_file_convenience(self, tmp_path):
        skill_dir = tmp_path / "conv-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(SAMPLE_SKILL_MD, encoding="utf-8")

        skill = load_skill_from_file(skill_md)
        assert skill.manifest.name == "test-skill"


class TestDiscoverSkills:
    def test_discovers_flat(self, tmp_path):
        for i in range(3):
            d = tmp_path / f"skill-{i}"
            d.mkdir()
            (d / "SKILL.md").write_text(
                f"---\nname: skill-{i}\ndescription: Skill {i}\n---\n\nBody {i}",
                encoding="utf-8",
            )

        skills = discover_skills(tmp_path)
        assert len(skills) == 3

    def test_discovers_no_skills_in_empty_dir(self, tmp_path):
        skills = discover_skills(tmp_path)
        assert skills == []

    def test_no_recurse(self, tmp_path):
        (tmp_path / "SKILL.md").write_text(
            "---\nname: top\ndescription: Top level\n---\n\nBody"
        )
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "SKILL.md").write_text(
            "---\nname: nested\ndescription: Nested\n---\n\nBody"
        )

        skills = discover_skills(tmp_path, recurse=True)
        assert len(skills) == 2

        skills_flat = discover_skills(tmp_path, recurse=False)
        assert len(skills_flat) == 1
        assert skills_flat[0].manifest.name == "top"
