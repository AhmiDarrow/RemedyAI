"""Adapter for Hermes-style skills.

Maps Hermes SKILL.md format (which is already close to agentskills.io)
into the native Remedy Skill model. Hermes skills may have slight
differences in frontmatter conventions; this adapter handles them.
"""

from __future__ import annotations

from pathlib import Path

from remedy.models import Skill, SkillKind
from remedy.skills.loader import (
    SkillLoadError,
    _build_manifest,
    _discover_bundled_resources,
    _parse_skill_md,
)


def load_hermes_skill(skill_dir: str | Path) -> Skill:
    """Load a Hermes-format skill and adapt it to Remedy's Skill model.

    Hermes skills store their SKILL.md at the root of the skill directory
    with YAML frontmatter. The format is ~95-99% compatible with
    agentskills.io -- this adapter handles the remaining differences:

    - Hermes may use `hermes_version` instead of `version`, or
      `skill_name` instead of `name`.
    - Hermes skills may reference Hermes-specific tool names that
      should be rewritten or mapped to MCP equivalents.

    Args:
        skill_dir: Path to the Hermes skill directory.

    Returns:
        A Remedy Skill ready for registration.
    """
    root = Path(skill_dir).expanduser().resolve()
    if not root.is_dir():
        raise SkillLoadError(f"Hermes skill directory not found: {root}")

    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise SkillLoadError(f"No SKILL.md found in Hermes skill dir: {root}")

    frontmatter, body = _parse_skill_md(skill_md)

    # Normalize Hermes-specific field names
    if "name" not in frontmatter and "skill_name" in frontmatter:
        frontmatter["name"] = frontmatter.pop("skill_name")
    if "version" not in frontmatter and "hermes_version" in frontmatter:
        frontmatter["version"] = frontmatter.pop("hermes_version")

    manifest = _build_manifest(frontmatter, SkillKind.HERMES, skill_md)
    scripts, refs = _discover_bundled_resources(root)

    return Skill(
        manifest=manifest,
        instructions=body,
        scripts=scripts,
        references=refs,
        source_skill_dir=str(root),
    )


def discover_hermes_skills(base_dir: str | Path) -> list[Skill]:
    """Scan a Hermes skills directory for all valid skills.

    Typical Hermes layout: ~/.hermes/skills/<skill-name>/SKILL.md
    """
    base = Path(base_dir).expanduser().resolve()
    if not base.is_dir():
        return []

    skills: list[Skill] = []
    for skill_dir in base.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        try:
            skills.append(load_hermes_skill(skill_dir))
        except SkillLoadError:
            continue

    return skills
