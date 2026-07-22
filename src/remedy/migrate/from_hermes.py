"""Migration tools for importing skills and configs from Hermes and OpenClaw.

Helps users transition from either system into Remedy with minimal friction.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from remedy.skills.adapters.hermes_adapter import discover_hermes_skills
from remedy.skills.adapters.openclaw_mcp_adapter import discover_openclaw_skills
from remedy.skills.registry import SkillRegistry


class MigrationResult:
    """Results of a migration operation."""

    def __init__(self) -> None:
        self.skills_imported: int = 0
        self.skills_skipped: int = 0
        self.errors: list[str] = []

    @property
    def total_processed(self) -> int:
        return self.skills_imported + self.skills_skipped

    def to_dict(self) -> dict:
        return {
            "skills_imported": self.skills_imported,
            "skills_skipped": self.skills_skipped,
            "errors": self.errors,
        }


def migrate_from_hermes(
    registry: SkillRegistry,
    hermes_skills_dir: str | Path,
    copy_to_remedy: bool = True,
    remedy_skills_dir: Optional[str | Path] = None,
) -> MigrationResult:
    """Import skills from a Hermes Agent installation.

    Args:
        registry: Target Remedy SkillRegistry.
        hermes_skills_dir: Path to ~/.hermes/skills/ (or equivalent).
        copy_to_remedy: Whether to copy skill files into Remedy's skills dir.
        remedy_skills_dir: Destination for copied skills.

    Returns:
        MigrationResult with counts and errors.
    """
    result = MigrationResult()
    hermes_path = Path(hermes_skills_dir).expanduser().resolve()

    if not hermes_path.is_dir():
        result.errors.append(f"Hermes skills directory not found: {hermes_path}")
        return result

    dest = None
    if copy_to_remedy:
        dest = Path(remedy_skills_dir).expanduser().resolve() if remedy_skills_dir else None

    for skill in discover_hermes_skills(hermes_path):
        try:
            if dest and skill.source_skill_dir:
                src = Path(skill.source_skill_dir)
                dst = dest / src.name
                if not dst.exists():
                    shutil.copytree(str(src), str(dst))
                    skill.source_skill_dir = str(dst)

            registry.register(skill)
            result.skills_imported += 1
        except Exception as e:
            result.errors.append(f"Failed to import {skill.manifest.name}: {e}")
            result.skills_skipped += 1

    return result


def migrate_from_openclaw(
    registry: SkillRegistry,
    openclaw_skills_dir: str | Path,
    copy_to_remedy: bool = True,
    remedy_skills_dir: Optional[str | Path] = None,
) -> MigrationResult:
    """Import skills from an OpenClaw/ClawHub installation.

    Args:
        registry: Target Remedy SkillRegistry.
        openclaw_skills_dir: Path to the OpenClaw skills directory.
        copy_to_remedy: Whether to copy skill files.
        remedy_skills_dir: Destination directory.

    Returns:
        MigrationResult with counts and errors.
    """
    result = MigrationResult()
    oc_path = Path(openclaw_skills_dir).expanduser().resolve()

    if not oc_path.is_dir():
        result.errors.append(f"OpenClaw skills directory not found: {oc_path}")
        return result

    dest = None
    if copy_to_remedy:
        dest = Path(remedy_skills_dir).expanduser().resolve() if remedy_skills_dir else None

    for skill in discover_openclaw_skills(oc_path):
        try:
            if dest and skill.source_skill_dir:
                src = Path(skill.source_skill_dir)
                dst = dest / src.name
                if not dst.exists():
                    shutil.copytree(str(src), str(dst))
                    skill.source_skill_dir = str(dst)

            registry.register(skill)
            result.skills_imported += 1
        except Exception as e:
            result.errors.append(f"Failed to import {skill.manifest.name}: {e}")
            result.skills_skipped += 1

    return result
