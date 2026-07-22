"""Tests for the Hermes and OpenClaw adapters."""

from pathlib import Path

import pytest

from remedy.models import SkillKind
from remedy.skills.adapters.hermes_adapter import (
    discover_hermes_skills,
    load_hermes_skill,
)
from remedy.skills.adapters.openclaw_mcp_adapter import (
    discover_openclaw_skills,
    load_mcp_skill,
    load_openclaw_skill,
)
from remedy.skills.loader import SkillLoadError


HERMES_SKILL = """---
name: hermes-test
description: A Hermes format skill
hermes_version: 2.0.0
author: HermesBot
tags:
  - legacy
---

# Hermes Skill

This skill was originally written for Hermes Agent.
"""

HERMES_SKILL_ALT = """---
skill_name: hermes-alt
description: Using legacy field names
version: 1.0.0
---

# Alt format
"""


class TestHermesAdapter:
    def test_load_basic_hermes_skill(self, tmp_path):
        sd = tmp_path / "legacy-skill"
        sd.mkdir()
        (sd / "SKILL.md").write_text(HERMES_SKILL)

        skill = load_hermes_skill(str(sd))
        assert skill.manifest.name == "hermes-test"
        assert skill.manifest.version == "2.0.0"
        assert skill.manifest.kind == SkillKind.HERMES
        assert "legacy" in skill.manifest.tags

    def test_normalizes_legacy_fields(self, tmp_path):
        sd = tmp_path / "alt-skill"
        sd.mkdir()
        (sd / "SKILL.md").write_text(HERMES_SKILL_ALT)

        skill = load_hermes_skill(str(sd))
        assert skill.manifest.name == "hermes-alt"

    def test_load_nonexistent_dir(self):
        with pytest.raises(SkillLoadError):
            load_hermes_skill("/nonexistent/hermes/dir")

    def test_discover_hermes_skills(self, tmp_path):
        for name in ["h1", "h2"]:
            sd = tmp_path / name
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Hermes {name}\n---\n\nBody"
            )

        (tmp_path / ".hidden").mkdir()
        (tmp_path / "not-a-skill").mkdir()

        skills = discover_hermes_skills(str(tmp_path))
        assert len(skills) == 2
        names = {s.manifest.name for s in skills}
        assert names == {"h1", "h2"}

    def test_discover_empty_dir(self, tmp_path):
        skills = discover_hermes_skills(str(tmp_path))
        assert skills == []

    def test_discover_nonexistent_dir(self):
        skills = discover_hermes_skills("/nonexistent")
        assert skills == []


OPENCLAW_SKILL_MD = """---
name: oc-skill
description: An OpenClaw skill
tags:
  - chat
  - automation
---

# OpenClaw Instructions

Set up the gateway configuration.
"""


class TestOpenClawAdapter:
    def test_load_openclaw_skill_md(self, tmp_path):
        sd = tmp_path / "oc-test"
        sd.mkdir()
        (sd / "SKILL.md").write_text(OPENCLAW_SKILL_MD)

        skill = load_openclaw_skill(str(sd))
        assert skill.manifest.name == "oc-skill"
        assert skill.manifest.kind == SkillKind.OPENCLAW
        assert "chat" in skill.manifest.tags

    def test_load_skill_md_alternate(self, tmp_path):
        sd = tmp_path / "oc-alt"
        sd.mkdir()
        (sd / "skill.md").write_text(OPENCLAW_SKILL_MD)

        skill = load_openclaw_skill(str(sd))
        assert skill.manifest.name == "oc-skill"

    def test_load_skill_yaml(self, tmp_path):
        import yaml

        sd = tmp_path / "oc-yaml"
        sd.mkdir()
        frontmatter = {
            "title": "yaml-skill",
            "description": "YAML-based skill",
            "version": "1.0.0",
        }
        (sd / "skill.yaml").write_text(yaml.dump(frontmatter))

        skill = load_openclaw_skill(str(sd))
        assert skill.manifest.name == "yaml-skill"

    def test_no_manifest_found(self, tmp_path):
        sd = tmp_path / "empty-oc"
        sd.mkdir()
        with pytest.raises(SkillLoadError):
            load_openclaw_skill(str(sd))

    def test_discover_openclaw_skills(self, tmp_path):
        for name in ["oc1", "oc2"]:
            sd = tmp_path / name
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: OC {name}\n---\n\nBody"
            )

        skills = discover_openclaw_skills(str(tmp_path))
        assert len(skills) == 2


class TestMCPSkill:
    def test_load_mcp_skill_synthetic(self):
        skill = load_mcp_skill("test-server", ["tool-a", "tool-b"])
        assert skill.manifest.name == "mcp-test-server"
        assert skill.manifest.kind == SkillKind.MCP
        assert "tool-a" in skill.manifest.tools
        assert "tool-b" in skill.manifest.tools
        assert "mcp" in skill.manifest.tags
        assert "test-server" in skill.manifest.tags
