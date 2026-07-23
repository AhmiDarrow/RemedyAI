"""Skill loading, discovery, and validation engine.

Implements full agentskills.io compliance with adapters for
Hermes and OpenClaw/MCP skill formats.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from remedy.models import Skill, SkillKind, SkillManifest, SkillStatus


class SkillLoadError(Exception):
    """Raised when a skill cannot be loaded or validated."""


def _parse_skill_md(path: Path) -> tuple[dict, str]:
    """Parse a SKILL.md file into (frontmatter_dict, markdown_body).

    Supports YAML frontmatter delimited by `---` on its own lines.
    """
    raw = path.read_text(encoding="utf-8")
    frontmatter: dict = {}
    body = raw

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if fm_match:
        try:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError as e:
            raise SkillLoadError(f"Invalid YAML frontmatter in {path}: {e}") from e
        body = raw[fm_match.end() :].strip()

    return frontmatter, body


def _build_manifest(frontmatter: dict, kind: SkillKind, path: Path) -> SkillManifest:
    """Build a SkillManifest from parsed frontmatter, filling defaults."""
    if "name" not in frontmatter:
        raise SkillLoadError(
            f"SKILL.md at {path} is missing required 'name' field in frontmatter"
        )
    if "description" not in frontmatter:
        raise SkillLoadError(
            f"SKILL.md at {path} is missing required 'description' field in frontmatter"
        )

    return SkillManifest(
        name=frontmatter["name"],
        description=frontmatter["description"],
        version=str(frontmatter.get("version", "1.0.0")),
        author=frontmatter.get("author"),
        license=frontmatter.get("license"),
        tags=frontmatter.get("tags", []) or [],
        kind=kind,
        status=SkillStatus.DISCOVERED,
        homepage=frontmatter.get("homepage"),
        repository=frontmatter.get("repository"),
        requires=frontmatter.get("requires", []) or [],
        tools=frontmatter.get("tools", []) or [],
        raw_frontmatter=frontmatter,
        path=str(path.parent),
    )


def _discover_bundled_resources(skill_dir: Path) -> tuple[list[str], list[str]]:
    """Find scripts/ and references/ directories inside a skill dir."""
    scripts: list[str] = []
    refs: list[str] = []

    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        scripts = sorted(
            str(p.relative_to(skill_dir)) for p in scripts_dir.rglob("*") if p.is_file()
        )

    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        refs = sorted(
            str(p.relative_to(skill_dir)) for p in refs_dir.rglob("*") if p.is_file()
        )

    return scripts, refs


def load_skill_from_dir(skill_dir: Path) -> Skill:
    """Load a single agentskills.io skill from a directory containing SKILL.md.

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        A validated Skill object ready for registration.

    Raises:
        SkillLoadError: If SKILL.md is missing, malformed, or invalid.
    """
    if not skill_dir.is_dir():
        raise SkillLoadError(f"Skill directory not found: {skill_dir}")

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillLoadError(
            f"No SKILL.md found in {skill_dir}. "
            f"Ensure the skill follows the agentskills.io format."
        )

    frontmatter, body = _parse_skill_md(skill_md)
    manifest = _build_manifest(frontmatter, SkillKind.NATIVE, skill_md)
    scripts, refs = _discover_bundled_resources(skill_dir)

    return Skill(
        manifest=manifest,
        instructions=body,
        scripts=scripts,
        references=refs,
        source_skill_dir=str(skill_dir.resolve()),
    )


def load_skill_from_file(skill_md_path: Path) -> Skill:
    """Convenience: load a skill given the path to its SKILL.md."""
    return load_skill_from_dir(skill_md_path.parent)


def discover_skills(root: Path, recurse: bool = True) -> list[Skill]:
    """Walk a directory tree and load all SKILL.md files found.

    Args:
        root: Root directory to search.
        recurse: Whether to search subdirectories.

    Returns:
        List of successfully loaded Skill objects.
    """
    skills: list[Skill] = []
    pattern = "**/SKILL.md" if recurse else "SKILL.md"

    for skill_md in root.glob(pattern):
        try:
            skill = load_skill_from_dir(skill_md.parent)
            skills.append(skill)
        except SkillLoadError:
            continue

    return skills


def parse_yaml_file(path: Path) -> dict[str, Any]:
    """Parse a YAML config or manifest file, returning empty dict on failure."""
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        return yaml.safe_load(raw) or {}
    except (yaml.YAMLError, OSError):
        return {}


def discover_skills_flat(base_dir: Path, loader_fn: Callable[[Path], Skill]) -> list[Skill]:
    """Scan a directory for skill subdirs and load using the given loader function.

    Non-recursive — scans only immediate subdirectories.
    """
    base = Path(base_dir).expanduser().resolve()
    if not base.is_dir():
        return []

    skills: list[Skill] = []
    for skill_dir in base.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        try:
            skills.append(loader_fn(skill_dir))
        except SkillLoadError:
            continue

    return skills
