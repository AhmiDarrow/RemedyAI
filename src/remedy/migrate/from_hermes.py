"""Migration tools for importing skills and configs from Hermes and OpenClaw.

Helps users transition from either system into Remedy with minimal friction.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from remedy.models import Skill
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


def _migrate_from(
    source_label: str,
    registry: SkillRegistry,
    skills_dir: str | Path,
    discover_fn: Callable[[Path], list[Skill]],
    copy_to_remedy: bool = True,
    remedy_skills_dir: str | Path | None = None,
) -> MigrationResult:
    result = MigrationResult()
    src_path = Path(skills_dir).expanduser().resolve()

    if not src_path.is_dir():
        result.errors.append(f"{source_label} skills directory not found: {src_path}")
        return result

    dest = None
    if copy_to_remedy:
        dest = Path(remedy_skills_dir).expanduser().resolve() if remedy_skills_dir else None

    for skill in discover_fn(src_path):
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


def migrate_from_hermes(
    registry: SkillRegistry,
    hermes_skills_dir: str | Path,
    copy_to_remedy: bool = True,
    remedy_skills_dir: str | Path | None = None,
) -> MigrationResult:
    """Import skills from a Hermes Agent installation."""
    return _migrate_from(
        "Hermes",
        registry,
        hermes_skills_dir,
        discover_hermes_skills,
        copy_to_remedy,
        remedy_skills_dir,
    )


def migrate_from_openclaw(
    registry: SkillRegistry,
    openclaw_skills_dir: str | Path,
    copy_to_remedy: bool = True,
    remedy_skills_dir: str | Path | None = None,
) -> MigrationResult:
    """Import skills from an OpenClaw/ClawHub installation."""
    return _migrate_from(
        "OpenClaw",
        registry,
        openclaw_skills_dir,
        discover_openclaw_skills,
        copy_to_remedy,
        remedy_skills_dir,
    )
